"""Architect: architecture, API contract, data schema, or a brownfield change design."""
from __future__ import annotations

import json
import re

from ..knowledge.url_shortener import blueprint
from ..models import AgentResult, Task
from ..orchestrator.engine import NonRetriableError
from .base import Agent


class Architect(Agent):
    name = "architect"
    role = "Software Architect"

    def run_offline(self, task: Task, ctx) -> AgentResult:
        spec = self.spec(ctx)
        mode = task.params["mode"]
        if mode == "change":
            return self._change_design(task, ctx, spec)
        if spec.domain != "url_shortener":
            raise NonRetriableError(f"no offline blueprint for domain '{spec.domain}'; run with --llm anthropic")
        opts = spec.options
        if mode == "architecture":
            arch = blueprint.architecture(opts)
            return AgentResult(outputs={"architecture": arch},
                               notes=[f"{len(arch['components'])} components, {len(arch['decisions'])} decisions"])
        if mode == "contract":
            contract = blueprint.openapi(opts)
            ops = sum(len(v) for v in contract["paths"].values())
            return AgentResult(outputs={"api_contract": contract},
                               files={"api/openapi.json": json.dumps(contract, indent=2) + "\n"},
                               notes=[f"{ops} operations"])
        if mode == "schema":
            ddl = blueprint.schema_sql(opts)
            tables = re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", ddl)
            return AgentResult(outputs={"data_schema": {"path": "shortener/storage/schema.sql", "tables": tables}},
                               files={"shortener/storage/schema.sql": ddl}, notes=[f"tables: {tables}"])
        raise ValueError(f"unknown architect mode {mode!r}")

    # ---------------------------------------------------------- brownfield
    def _change_design(self, task: Task, ctx, spec) -> AgentResult:
        impact = ctx.blackboard.require("impact")
        codebase = ctx.blackboard.require("codebase")
        req = spec.raw.lower()
        wants_expiry = bool(re.search(r"expir|ttl", req))
        wants_fix = bool(re.search(r"overwrite|duplicate|collision|bug", req))
        changes = []
        for m in impact["impacted_modules"]:
            info = codebase.get(m["path"], {})
            sql = " ".join(info.get("sql", [])).upper()
            plan = []
            if "CREATE TABLE" in sql and wants_expiry:
                plan.append("add nullable expires_at column; idempotent forward migration for existing databases")
            if "INSERT OR REPLACE" in sql and wants_fix:
                plan.append("replace INSERT OR REPLACE with strict INSERT; raise DuplicateCode on conflict")
            if ("INSERT" in sql or "SELECT" in sql) and wants_expiry and "CREATE TABLE" not in sql:
                plan.append("persist and return expires_at")
            if m["path"] in impact["entry_points"]:
                if wants_expiry:
                    plan.append("accept optional ttl_seconds; map expired links to the agreed status code")
            elif info.get("functions") and not info.get("sql") and "resolve" in " ".join(info["functions"]):
                if wants_expiry:
                    plan.append("validate ttl; compute expires_at; reject expired links on resolve")
                if wants_fix:
                    plan.append("retry code generation on DuplicateCode with a bounded number of attempts")
            if not plan:
                plan.append("no change expected; collisions are handled by the caller" if "code" in m["path"]
                            else "review only")
            changes.append({"module": m["path"], "changes": plan, "confidence": m["confidence"]})
        options = spec.options
        design = {
            "summary": spec.intent,
            "module_changes": changes,
            "api_changes": ([f"POST /shorten: new optional field ttl_seconds ({options.get('ttl_bounds', 'bounded integer')})",
                             f"GET /<code>: expired -> {options.get('expired_status', '410 Gone')}"] if wants_expiry else []),
            "data_migration": "additive nullable column, applied automatically on connect; no backfill needed"
            if wants_expiry else "none",
            "compatibility": "backward compatible: existing requests/responses unchanged, old databases migrate in place",
            "test_strategy": ["regression test reproducing the overwrite bug", "unit tests for TTL boundaries",
                              "HTTP-level test for expiry status", "migration test on a v1-schema database",
                              "full existing suite must keep passing (regression gate vs baseline)"],
        }
        return AgentResult(outputs={"change_design": design},
                           notes=[f"{sum(c['changes'] != ['review only'] for c in changes)} modules to change"])

    # ----------------------------------------------------------------- LLM
    def run_llm(self, task: Task, ctx) -> AgentResult:
        spec = self.spec(ctx)
        mode = task.params["mode"]
        if mode == "architecture":
            arch = self.ask(ctx, task, "Design the architecture.", '{"style": "...", "components": [{"name": "...", '
                            '"module": "path", "responsibility": "..."}], "decisions": [{"decision": "...", '
                            '"alternatives": "...", "rationale": "..."}], "data_model": {"table": ["col"]}, '
                            '"scaling_path": ["..."]}', Spec=spec.to_dict(),
                            Reference_example=blueprint.architecture(spec.options) if spec.domain == "url_shortener" else "n/a")
            return AgentResult(outputs={"architecture": arch})
        if mode == "contract":
            contract = self.ask(ctx, task, "Write the OpenAPI 3.0 contract for the HTTP API.", "<OpenAPI 3.0 JSON>",
                                Spec=spec.to_dict(), Architecture=ctx.blackboard.get("architecture"))
            if "paths" not in contract:
                raise ValueError("contract has no 'paths'")
            return AgentResult(outputs={"api_contract": contract},
                               files={"api/openapi.json": json.dumps(contract, indent=2) + "\n"})
        if mode == "schema":
            data = self.ask(ctx, task, "Write the SQL DDL (SQLite dialect, idempotent).",
                            '{"path": "relative/path/schema.sql", "ddl": "...", "tables": ["..."]}',
                            Architecture=ctx.blackboard.get("architecture"))
            return AgentResult(outputs={"data_schema": {"path": data["path"], "tables": data["tables"]}},
                               files={data["path"]: data["ddl"]})
        if mode == "change":
            base = self._change_design(task, ctx, spec)
            refined = self.ask(ctx, task, "Refine this change design for the existing codebase.",
                               '{"summary": "...", "module_changes": [{"module": "path", "changes": ["..."]}], '
                               '"api_changes": [], "data_migration": "...", "compatibility": "...", "test_strategy": []}',
                               Spec=spec.to_dict(), Impact=ctx.blackboard.get("impact"),
                               Codebase=ctx.blackboard.get("codebase"), Draft=base.outputs["change_design"])
            return AgentResult(outputs={"change_design": refined})
        raise ValueError(f"unknown architect mode {mode!r}")
