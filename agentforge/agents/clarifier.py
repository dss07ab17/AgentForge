"""Clarifier: runs gate G1 and folds answers into the spec as design options + assumptions."""
from __future__ import annotations

from ..hitl import ClarificationNeeded
from ..knowledge.domains import OPTION_APPLIERS
from ..models import AgentResult, Task
from ..orchestrator.engine import NonRetriableError
from .base import Agent


class ClarificationHalt(NonRetriableError):
    pass


class Clarifier(Agent):
    name = "clarifier"
    role = "Clarifier"

    def run_offline(self, task: Task, ctx) -> AgentResult:
        spec = self.spec(ctx)
        ctx.events.emit("gate", f"G1 clarification gate: {len(spec.ambiguities)} question(s), "
                                f"{len(spec.blocking_ambiguities)} blocking", gate="clarify")
        try:
            ctx.gate.clarify(spec.ambiguities)
        except ClarificationNeeded as exc:
            ctx.blackboard.put("clarification_request", [a.to_dict() for a in exc.questions], author=task.id)
            ctx.blackboard.put("spec_draft", spec.to_dict(), author=task.id)
            raise ClarificationHalt(str(exc)) from None

        options: dict = {}
        assumptions: list[str] = []
        for amb in spec.ambiguities:
            applier = OPTION_APPLIERS.get(amb.id)
            if applier:
                options.update(applier(amb.resolution))
            else:
                options[amb.id] = amb.resolution
            label = {"default": "Assumed (default)", "human": "Decided by human",
                     "answers-file": "Decided by human (answers file)"}[amb.resolved_by]
            assumptions.append(f"{label}: {amb.question} -> {amb.resolution}")
            ctx.events.emit("gate.decision", f"{amb.id}: {amb.resolution} [{amb.resolved_by}]", gate="clarify")

        if options.get("confirmed_domain", "yes").lower().startswith("no"):
            raise ClarificationHalt("human says the requirement is not a URL shortener; please restate it "
                                    "(or run with --llm anthropic for open-domain requirements)")
        if options.get("user_accounts") == "in scope":
            spec.out_of_scope = [s for s in spec.out_of_scope if "account" not in s.lower()]
            spec.functional.append("User accounts and per-user link ownership")
            assumptions.append("LIMITATION: user accounts were requested; the offline blueprint does not implement "
                               "them - planned as a separate work item (see risks).")
        if "latency_target" in options:
            spec.non_functional.append(f"Performance: {options['latency_target']}")
        if "scale_target" in options:
            spec.non_functional.append(f"Scale target: {options['scale_target']}")
        spec.options = {**spec.options, **options}
        spec.assumptions = assumptions
        return AgentResult(outputs={"spec": spec.to_dict()},
                           notes=[f"{sum(a.resolved_by == 'default' for a in spec.ambiguities)} defaulted, "
                                  f"{sum(a.resolved_by != 'default' for a in spec.ambiguities)} answered"])

