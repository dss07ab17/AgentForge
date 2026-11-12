"""Coder: implements one component (greenfield) or edits specific files (brownfield).

Cross-step coordination: the coder reads the architect's published contract and
schema from the blackboard and refuses to hand back code that disagrees with
them (e.g. an API whose routes do not match the OpenAPI contract), so contract
drift is caught at the step that causes it rather than at the end.
"""
from __future__ import annotations

import ast
import json

from ..knowledge.brownfield import linkshort_recipe
from ..knowledge.url_shortener import blueprint
from ..models import AgentResult, RequirementType, Task
from ..orchestrator.engine import NonRetriableError
from .base import Agent


class ContractMismatch(RuntimeError):
    pass


def routes_in(source: str) -> set[tuple[str, str]]:
    """Extract ROUTES = [(method, path, handler), ...] statically (no import/exec)."""
    for node in ast.walk(ast.parse(source)):
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        if any(isinstance(t, ast.Name) and t.id == "ROUTES" for t in targets):
            return {(m, p) for m, p, *_ in ast.literal_eval(node.value)}
    return set()


def contract_operations(contract: dict) -> set[tuple[str, str]]:
    return {(method.upper(), path) for path, ops in contract.get("paths", {}).items() for method in ops}


def check_contract(files: dict[str, str], contract: dict, api_path: str = "shortener/api.py") -> None:
    if api_path not in files:
        return
    implemented, documented = routes_in(files[api_path]), contract_operations(contract)
    if implemented != documented:
        raise ContractMismatch(f"routes differ from contract: missing={sorted(documented - implemented)} "
                               f"undocumented={sorted(implemented - documented)}")


def repo_snapshot(ctx) -> dict[str, str]:
    base = ctx.workspace.baseline or ctx.workspace.root
    return {p.relative_to(base).as_posix(): p.read_text(encoding="utf-8")
            for p in base.rglob("*.py")} | ({"README.md": (base / "README.md").read_text(encoding="utf-8")}
                                            if (base / "README.md").exists() else {})


class Coder(Agent):
    name = "coder"
    role = "Senior Software Engineer"

    def run_offline(self, task: Task, ctx) -> AgentResult:
        spec = self.spec(ctx)
        if spec.req_type != RequirementType.GREENFIELD:
            return self._brownfield_offline(task, ctx)
        if spec.domain != "url_shortener":
            raise NonRetriableError(f"no offline implementation for domain '{spec.domain}'; run with --llm anthropic")
        component = task.params["component"]
        files = blueprint.render(component, spec.options)
        if component == "storage":
            schema = ctx.blackboard.require("data_schema")
            if not ctx.workspace.exists(schema["path"]):
                raise RuntimeError(f"schema {schema['path']} not found in workspace; architect step incomplete")
            missing = [t for t in schema["tables"] if t not in files["shortener/storage/sqlite_repo.py"]]
            if missing:
                raise ContractMismatch(f"repository does not use schema tables {missing}")
        if component == "api":
            check_contract(files, ctx.blackboard.require("api_contract"))
        if ctx.faults.should_corrupt(task):
            files = blueprint.corrupt(files)
            ctx.events.emit("info", f"{task.id}: fault injection planted a defect (validator should catch it)")
        return AgentResult(files=files, notes=[f"{len(files)} files: {', '.join(sorted(files))}"])

    def _brownfield_offline(self, task: Task, ctx) -> AgentResult:
        snapshot = repo_snapshot(ctx)
        if not linkshort_recipe.matches(snapshot):
            raise NonRetriableError("offline mode has no change recipe for this repository; run with --llm anthropic")
        targets = set(task.params["files"])
        edits = [e for group in ("storage", "service", "web") for e in linkshort_recipe.GROUPS[group]
                 if e["file"] in targets]
        if not edits:
            return AgentResult(notes=[f"no change needed in {', '.join(sorted(targets))} after design review"])
        return AgentResult(edits=edits, notes=[f"{len(edits)} anchored edit(s) to {', '.join(sorted(targets))}"])

    # ----------------------------------------------------------------- LLM
    def run_llm(self, task: Task, ctx) -> AgentResult:
        spec = self.spec(ctx)
        if spec.req_type != RequirementType.GREENFIELD:
            files = {f: ctx.workspace.read(f) for f in task.params["files"] if ctx.workspace.exists(f)}
            data = self.ask(ctx, task, "Make the minimal, correct change to these files. Each edit's 'old' text must "
                            "appear exactly once in the current file. Keep public interfaces backward compatible.",
                            '{"edits": [{"file": "path", "old": "exact existing text", "new": "replacement"}], '
                            '"notes": ["..."]}', Spec=spec.to_dict(), Change_design=ctx.blackboard.get("change_design"),
                            Current_files=files)
            return AgentResult(edits=data["edits"], notes=data.get("notes", []))
        component = task.params["component"]
        data = self.ask(ctx, task, f"Implement the '{component}' component. Return complete file contents. Follow "
                        "the architecture and the API contract exactly; standard library only.",
                        '{"files": {"relative/path.py": "full file content"}, "notes": ["..."]}',
                        Spec=spec.to_dict(), Architecture=ctx.blackboard.get("architecture"),
                        API_contract=ctx.blackboard.get("api_contract"), Data_schema=ctx.blackboard.get("data_schema"),
                        Existing_files=ctx.workspace.list_files())
        files = data["files"]
        contract = ctx.blackboard.get("api_contract")
        if contract and any(p.endswith("api.py") for p in files):
            api_path = next(p for p in files if p.endswith("api.py"))
            if routes_in(files[api_path]):
                check_contract(files, contract, api_path)
        return AgentResult(files=files, notes=data.get("notes", []) + [json.dumps(sorted(files))])
