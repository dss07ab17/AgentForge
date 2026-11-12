"""Agent base class.

An agent is a stateless specialist: it reads what it needs from the blackboard,
does one job, and returns an ``AgentResult``. Each agent has an offline
(deterministic) strategy and an LLM strategy; ``run`` picks one based on the
provider. Agents never write files directly - they return them, and the engine
applies them through the guarded workspace.
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any

from ..context import RunContext
from ..models import AgentResult, Spec, Task


class Agent(ABC):
    name = "agent"
    role = ""

    def run(self, task: Task, ctx: RunContext) -> AgentResult:
        ctx.faults.maybe_raise(task)
        if ctx.llm.is_live:
            return self.run_llm(task, ctx)
        return self.run_offline(task, ctx)

    @abstractmethod
    def run_offline(self, task: Task, ctx: RunContext) -> AgentResult: ...

    def run_llm(self, task: Task, ctx: RunContext) -> AgentResult:
        return self.run_offline(task, ctx)

    # ------------------------------------------------------------- helpers
    @staticmethod
    def spec(ctx: RunContext) -> Spec:
        return Spec.from_dict(ctx.blackboard.require("spec"))

    def ask(self, ctx: RunContext, task: Task, prompt: str, shape: str, **context: Any) -> Any:
        """Call the LLM with role, task, shared context and (on retry) the previous error."""
        system = (f"You are the {self.role} in an autonomous software-engineering pipeline. "
                  "Your output is consumed by other agents and validated automatically, so follow the JSON "
                  "shape exactly. Write production-quality, secure, maintainable code. Never use eval/exec, "
                  "shell commands, or hard-coded secrets.")
        parts = [f"## Task\n{task.title}\n{task.description}", prompt]
        for key, value in context.items():
            rendered = value if isinstance(value, str) else json.dumps(value, indent=1, default=str)
            parts.append(f"## {key}\n{rendered[:30000]}")
        if task.error and task.attempts > 1:
            parts.append(f"## Your previous attempt failed\n{task.error}\nFix the problem in this attempt.")
        return ctx.llm.complete_json(system, "\n\n".join(parts), shape=shape)
