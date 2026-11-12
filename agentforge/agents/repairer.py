"""Repairer: turns validation failures into fixes.

Guardrail: the repairer fixes *code*, never the tests or the contract - those
are the specification. Any attempt to modify ``tests/`` or ``api/`` is rejected.

Offline: failing tests and traceback paths are mapped back to the owning
component(s), which are regenerated from the spec (fresh generation with the
failure as context). LLM: the failing output and implicated files are sent to
the model, which returns minimal anchored edits.
"""
from __future__ import annotations

import re

from ..knowledge.url_shortener import blueprint
from ..models import AgentResult, RequirementType, Task
from ..orchestrator.engine import NonRetriableError
from .base import Agent

TEST_TARGETS = {
    "test_api": ["api", "service"], "test_http_server": ["api", "analytics", "storage"], "test_contract": ["api"],
    "test_service": ["service"], "test_analytics": ["analytics", "storage"], "test_cache": ["storage"],
    "test_codec_and_idgen": ["core"], "test_validation": ["core"], "test_ratelimit": ["service"],
}
PROTECTED_PREFIXES = ("tests/", "api/")


def implicated(validation: dict) -> tuple[set[str], set[str]]:
    """Return (components, source files) implicated by failing checks."""
    components: set[str] = set()
    files: set[str] = set()
    for check in validation["checks"]:
        if check["passed"] or not check.get("blocking", True):
            continue
        text = check["details"] + " " + " ".join(
            f["message"] + f["detail"] for f in check.get("data", {}).get("failures", []))
        text = text.replace("\\", "/")  # Windows pytest output reports paths with backslashes
        for path in re.findall(r"((?:shortener|app)/[\w/]+\.py)", text):
            files.add(path)
            if path in blueprint.OWNER:
                components.add(blueprint.OWNER[path])
        for module in re.findall(r"tests[./](?:unit|integration)?[./]?(test_\w+)", text):
            components.update(TEST_TARGETS.get(module, []))
        if check["name"] == "contract":
            components.add("api")
    return {c for c in components if not c.endswith("tests")}, files


class Repairer(Agent):
    name = "repairer"
    role = "Debugging Engineer"

    def run_offline(self, task: Task, ctx) -> AgentResult:
        spec = self.spec(ctx)
        validation = ctx.blackboard.require("validation")
        components, files = implicated(validation)
        if spec.req_type != RequirementType.GREENFIELD or spec.domain != "url_shortener":
            raise NonRetriableError("offline repair is only available for blueprint-generated code; "
                                    "run with --llm anthropic for model-driven repair")
        if not components:
            raise NonRetriableError("could not map failures to a component; human attention needed")
        regenerated: dict[str, str] = {}
        for comp in sorted(components):
            regenerated.update(blueprint.render(comp, spec.options))
        changed = {p: c for p, c in regenerated.items()
                   if not ctx.workspace.exists(p) or ctx.workspace.read(p) != c}
        notes = [f"implicated components: {sorted(components)}; files in tracebacks: {sorted(files) or 'none'}",
                 f"regenerated {len(changed)} changed file(s): {sorted(changed) or 'none'}"]
        if not changed:
            raise NonRetriableError("regeneration produced identical code; the failure is not a generation defect")
        return AgentResult(files=changed, outputs={f"repair_{task.params['iteration']}": notes}, notes=notes)

    def run_llm(self, task: Task, ctx) -> AgentResult:
        validation = ctx.blackboard.require("validation")
        _, files = implicated(validation)
        sources = {f: ctx.workspace.read(f) for f in files if ctx.workspace.exists(f)}
        if not sources:  # fall back to all non-test sources (bounded)
            candidates = [p for p in ctx.workspace.list_files(".py") if not p.startswith(PROTECTED_PREFIXES)]
            sources = {f: ctx.workspace.read(f) for f in candidates[:12]}
        data = self.ask(ctx, task, "Fix the code so the failing checks pass. Do NOT modify tests or the API contract. "
                        "Return minimal edits; each 'old' must appear exactly once in the file.",
                        '{"edits": [{"file": "path", "old": "...", "new": "..."}], "root_cause": "..."}',
                        Failing_checks=[c for c in validation["checks"] if not c["passed"]], Source_files=sources)
        edits = data["edits"]
        bad = [e["file"] for e in edits if e["file"].startswith(PROTECTED_PREFIXES)]
        if bad:
            raise ValueError(f"repair attempted to modify protected files {bad}; fix the implementation instead")
        return AgentResult(edits=edits, notes=[data.get("root_cause", "")])
