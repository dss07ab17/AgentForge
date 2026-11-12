"""Human approval gates G2 (plan) and G3 (release) as first-class tasks in the graph."""
from __future__ import annotations

from ..hitl import GateRejected
from ..models import AgentResult, Task
from ..orchestrator.engine import NonRetriableError
from .base import Agent


class GateAgent(Agent):
    name = "gate"
    role = "Approval gate"

    def run(self, task: Task, ctx) -> AgentResult:
        return self.run_offline(task, ctx)

    def run_offline(self, task: Task, ctx) -> AgentResult:
        gate = task.params["gate"]
        summary = self._plan_summary(ctx) if gate == "plan" else self._release_summary(ctx)
        ctx.events.emit("gate", f"{'G2' if gate == 'plan' else 'G3'} approval gate '{gate}'", gate=gate)
        try:
            ctx.gate.approve(gate, summary)
        except GateRejected as exc:
            raise NonRetriableError(str(exc)) from None
        decision = ctx.gate.audit[-1]
        ctx.events.emit("gate.decision", f"{gate}: {decision['decision']} by {decision['by']}", gate=gate)
        return AgentResult(outputs={f"gate_{gate}": decision})

    @staticmethod
    def _plan_summary(ctx) -> str:
        spec = ctx.blackboard.require("spec")
        lines = [f"Spec: {spec['title']} ({spec['req_type']}, domain={spec['domain']})", f"Intent: {spec['intent']}",
                 "Assumptions:"] + [f"  - {a}" for a in spec["assumptions"]] + ["Plan:"]
        for t in ctx.blackboard.require("plan"):
            lines.append(f"  - {t['id']} [{t['agent']}] {t['title']}  <- {', '.join(t['depends_on'])}")
        return "\n".join(lines)

    @staticmethod
    def _release_summary(ctx) -> str:
        val = ctx.blackboard.get("validation") or {}
        lines = ["Validation:"] + [f"  - {c['name']}: {'ok' if c['passed'] else 'FAIL'} ({c['details']})"
                                   for c in val.get("checks", [])]
        diff = ctx.workspace.diff()
        if diff:
            added = sum(1 for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++"))
            removed = sum(1 for line in diff.splitlines() if line.startswith("-") and not line.startswith("---"))
            lines.append(f"Diff: +{added} / -{removed} lines")
        else:
            lines.append(f"Generated files: {len(ctx.workspace.list_files())}")
        return "\n".join(lines)
