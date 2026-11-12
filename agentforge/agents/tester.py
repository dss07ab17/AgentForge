"""Test Engineer: writes unit / integration / regression tests (in parallel with coding)."""
from __future__ import annotations

import re

from ..knowledge.brownfield import linkshort_recipe
from ..knowledge.url_shortener import blueprint
from ..models import AgentResult, RequirementType, Task
from ..orchestrator.engine import NonRetriableError
from .base import Agent
from .coder import repo_snapshot


def contract_coverage(test_sources: dict[str, str], contract: dict) -> dict:
    """Which documented operations are exercised by at least one test request."""
    text = "\n".join(test_sources.values())
    covered, uncovered = [], []
    for path, ops in contract["paths"].items():
        regex = re.escape(path).replace(r"\{code\}", r"[^\"']*")
        for method in ops:
            hit = re.search(rf"[\"']{method.upper()}[\"'],\s*f?[\"']{regex}[\"'/]", text) or \
                (path == "/{code}" and re.search(r"\"GET\",\s*f\"/\{", text))
            (covered if hit else uncovered).append(f"{method.upper()} {path}")
    return {"covered": covered, "uncovered": uncovered,
            "ratio": round(len(covered) / max(1, len(covered) + len(uncovered)), 2)}


class Tester(Agent):
    name = "tester"
    role = "Test Engineer"

    def run_offline(self, task: Task, ctx) -> AgentResult:
        spec = self.spec(ctx)
        component = task.params["component"]
        if spec.req_type != RequirementType.GREENFIELD:
            if not linkshort_recipe.matches(repo_snapshot(ctx)):
                raise NonRetriableError("offline mode has no test recipe for this repository; run with --llm anthropic")
            edits = linkshort_recipe.TEST_EDITS
            n = sum(e["new"].count("\ndef test_") for e in edits)
            return AgentResult(edits=edits, outputs={"test_plan_brownfield": {"new_tests": n}},
                               notes=[f"{n} new test functions"])
        if spec.domain != "url_shortener":
            raise NonRetriableError(f"no offline test blueprint for domain '{spec.domain}'")
        files = blueprint.render(component, spec.options)
        n = sum(src.count("\ndef test_") for src in files.values())
        outputs = {f"test_plan_{component}": {"files": sorted(files), "test_functions": n}}
        notes = [f"{n} test functions in {len(files)} files"]
        if component == "integration_tests":
            cov = contract_coverage(files, ctx.blackboard.require("api_contract"))
            outputs["contract_coverage"] = cov
            notes.append(f"contract coverage {cov['ratio']:.0%} (uncovered: {cov['uncovered'] or 'none'})")
        return AgentResult(files=files, outputs=outputs, notes=notes)

    def run_llm(self, task: Task, ctx) -> AgentResult:
        spec = self.spec(ctx)
        data = self.ask(ctx, task, "Write pytest tests. Cover happy paths, validation errors, edge cases and failure "
                        "handling. Tests must be deterministic (inject clocks/randomness) and need no network.",
                        '{"files": {"tests/...py": "content"}}', Spec=spec.to_dict(),
                        Architecture=ctx.blackboard.get("architecture"), API_contract=ctx.blackboard.get("api_contract"),
                        Change_design=ctx.blackboard.get("change_design"), Existing_files=ctx.workspace.list_files())
        return AgentResult(files=data["files"], notes=[f"{len(data['files'])} test files"])
