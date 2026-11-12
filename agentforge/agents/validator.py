"""Validator: independent quality gate, plus the validate -> repair -> re-validate loop.

Checks (all run against the sandboxed workspace, never against the user's repo):
  syntax      every .py file parses
  policy      generated-code guardrails (defence in depth: re-scan the final tree)
  lint        ruff F/E9 rules (undefined names, unused imports, syntax) if ruff is available
  contract    implemented routes == OpenAPI operations (greenfield, static/AST based)
  tests       full pytest run in a subprocess with timeout and scrubbed env
  regression  every test that passed on the baseline still passes (brownfield)

On failure the validator *spawns* a repair task and a new validation task and
hands its dependents over to the new validation, so the release gate always
waits for the final verdict. The loop is bounded by the repair budget.
"""
from __future__ import annotations

import ast
import json
import re
import shutil
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from ..guardrails import run_command, scan_python, scan_secrets
from ..models import AgentResult, CheckResult, Task, ValidationReport
from ..orchestrator.engine import NonRetriableError
from .base import Agent
from .coder import contract_operations, routes_in


class ValidationFailed(NonRetriableError):
    pass


def run_pytest(root: Path, junit: Path, budgets) -> dict:
    root = root.resolve()
    # pin rootdir so test ids do not depend on where the run directory lives
    proc = run_command([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--tb=short",
                        f"--rootdir={root}", f"--junitxml={junit}"], root, budgets)
    results: dict[str, str] = {}
    failures: list[dict] = []
    if junit.exists():
        for case in ET.parse(junit).getroot().iter("testcase"):
            classname = re.sub(r"^.*?\btests\.", "tests.", case.get("classname") or "")
            name = f"{classname}::{case.get('name')}"
            bad = case.find("failure") if case.find("failure") is not None else case.find("error")
            if bad is not None:
                results[name] = "failed"
                failures.append({"test": name, "message": (bad.get("message") or "")[:300],
                                 "detail": (bad.text or "")[-1500:]})
            elif case.find("skipped") is not None:
                results[name] = "skipped"
            else:
                results[name] = "passed"
    passed = sum(v == "passed" for v in results.values())
    return {"exit_code": proc.returncode, "total": len(results), "passed": passed,
            "failed": len(failures), "results": results, "failures": failures,
            "summary": (proc.stdout.strip().splitlines() or [""])[-1][:200], "stderr": proc.stderr[-800:]}


class Validator(Agent):
    name = "validator"
    role = "Validation Engineer"

    def run(self, task: Task, ctx) -> AgentResult:  # validation is deterministic in every mode
        ctx.faults.maybe_raise(task)
        return self.run_offline(task, ctx)

    def run_offline(self, task: Task, ctx) -> AgentResult:
        if task.params.get("mode") == "baseline":
            return self._baseline(task, ctx)
        ws = ctx.workspace
        iteration = int(task.params.get("iteration", 1))
        checks = [self._syntax(ws), self._policy(ws), self._lint(ws)]
        contract = ctx.blackboard.get("api_contract")
        if contract and ws.exists("shortener/api.py"):
            checks.append(self._contract(ws, contract))
        tests = run_pytest(ws.root, ctx.run_dir / f"junit-{iteration}.xml", ctx.budgets)
        checks.append(CheckResult(
            "tests", passed=tests["exit_code"] == 0 and tests["failed"] == 0 and tests["total"] > 0,
            details=f"{tests['passed']}/{tests['total']} passed" + (
                f"; failing: {', '.join(f['test'].split('::')[-1] for f in tests['failures'][:4])}"
                if tests["failed"] else ("" if tests["total"] else f"; no tests ran: {tests['stderr'][-200:]}")),
            data={"failures": tests["failures"], "total": tests["total"], "passed": tests["passed"]}))
        baseline = ctx.blackboard.get("baseline_results")
        if baseline:
            checks.append(self._regression(baseline, tests))
        report = ValidationReport(iteration=iteration, checks=checks)
        history = (ctx.blackboard.get("validation_history") or []) + [report.to_dict()]
        outputs = {"validation": report.to_dict(), "validation_history": history,
                   "test_results": {k: v for k, v in tests.items() if k != "results"}}
        summary = "; ".join(f"{c.name}={'ok' if c.passed else 'FAIL'}" for c in checks)
        ctx.events.emit("validation", f"iteration {iteration}: {summary}", iteration=iteration)
        if report.passed:
            return AgentResult(outputs=outputs, notes=[summary])

        if iteration > ctx.budgets.max_repair_iterations:
            ctx.blackboard.put("validation", report.to_dict(), author=task.id)
            ctx.blackboard.put("validation_history", history, author=task.id)
            raise ValidationFailed(f"validation still failing after {ctx.budgets.max_repair_iterations} repair "
                                   f"iteration(s): {summary}")
        repair_id, next_id = f"repair_{iteration}", f"validate_{iteration + 1}"
        repair = Task(id=repair_id, title=f"Repair failures from validation #{iteration}", agent="repairer",
                      depends_on=[task.id], params={"iteration": iteration})
        revalidate = Task(id=next_id, title=f"Re-validate (iteration {iteration + 1})", agent="validator",
                          depends_on=[repair_id], params={"mode": "validate", "iteration": iteration + 1})
        return AgentResult(outputs=outputs, spawn=[repair, revalidate], handoff=next_id,
                           notes=[summary, f"spawned {repair_id} -> {next_id}"])

    # --------------------------------------------------------------- checks
    @staticmethod
    def _syntax(ws) -> CheckResult:
        errors = []
        for rel in ws.list_files(".py"):
            try:
                ast.parse(ws.read(rel), filename=rel)
            except SyntaxError as exc:
                errors.append(f"{rel}:{exc.lineno}: {exc.msg}")
        return CheckResult("syntax", not errors, "; ".join(errors) or f"{len(ws.list_files('.py'))} files parse")

    @staticmethod
    def _policy(ws) -> CheckResult:
        problems = []
        for rel in ws.list_files():
            if rel.endswith((".py", ".json", ".md", ".toml", ".sql", ".txt")):
                text = ws.read(rel)
                problems += scan_secrets(text, rel)
                if rel.endswith(".py"):
                    problems += [p for p in scan_python(text, rel) if "syntax error" not in p]
        return CheckResult("policy", not problems, "; ".join(problems[:5]) or "no forbidden constructs or secrets")

    @staticmethod
    def _lint(ws) -> CheckResult:
        if not shutil.which("ruff"):
            return CheckResult("lint", True, "ruff not installed - skipped", blocking=False)
        proc = run_command(["ruff", "check", "--no-cache", "--select", "F,E9", "--output-format", "concise", "-q", "."],
                           ws.root, ws.budgets)
        issues = [line for line in proc.stdout.splitlines() if line.strip()]
        return CheckResult("lint", not issues, "; ".join(issues[:5]) or "ruff F,E9 clean",
                           data={"issues": issues[:50]})

    @staticmethod
    def _contract(ws, contract: dict) -> CheckResult:
        implemented = routes_in(ws.read("shortener/api.py"))
        documented = contract_operations(contract)
        on_disk = contract_operations(json.loads(ws.read("api/openapi.json"))) if ws.exists("api/openapi.json") else set()
        ok = implemented == documented == on_disk
        details = (f"{len(implemented)} routes match {len(documented)} documented operations" if ok else
                   f"missing={sorted(documented - implemented)} undocumented={sorted(implemented - documented)}")
        return CheckResult("contract", ok, details)

    @staticmethod
    def _regression(baseline: dict, tests: dict) -> CheckResult:
        before = {k for k, v in baseline["results"].items() if v == "passed"}
        if not before:
            return CheckResult("regression", True, "baseline had no passing tests to compare against", blocking=False)
        regressed = sorted(k for k in before if tests["results"].get(k) != "passed")
        return CheckResult("regression", not regressed,
                           f"{len(before)} baseline tests still pass" if not regressed else f"regressed: {regressed}",
                           data={"baseline_passed": len(before), "regressed": regressed})

    def _baseline(self, task: Task, ctx) -> AgentResult:
        base = ctx.workspace.baseline
        if base is None:
            return AgentResult(outputs={"baseline_results": {"results": {}, "total": 0}})
        res = run_pytest(base, ctx.run_dir / "junit-baseline.xml", ctx.budgets)
        return AgentResult(outputs={"baseline_results": res},
                           notes=[f"baseline: {res['passed']}/{res['total']} passing before any change"])
