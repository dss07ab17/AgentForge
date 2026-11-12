"""Run context handed to every agent, plus deterministic fault injection."""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path

from .guardrails import Budgets
from .hitl import Gatekeeper
from .llm.base import LLMProvider
from .models import Task
from .orchestrator.blackboard import Blackboard
from .orchestrator.events import EventLog
from .workspace import Workspace


class InjectedTransientError(RuntimeError):
    """Simulates a flaky dependency (LLM timeout, rate limit) for recovery demos."""


@dataclass
class FaultInjector:
    """Deterministic faults so error handling can be demonstrated and tested.

    spec format: ``<task_id>:<kind>`` where kind is
      transient - the task's first attempt raises (exercises retry/backoff)
      bug       - the task's first output is subtly broken (exercises the
                  validate -> repair -> re-validate loop)
    """

    specs: dict[str, str] = field(default_factory=dict)
    _fired: set[str] = field(default_factory=set)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @classmethod
    def parse(cls, items: list[str] | None) -> "FaultInjector":
        specs = {}
        for item in items or []:
            task_id, _, kind = item.partition(":")
            specs[task_id] = kind or "transient"
        return cls(specs=specs)

    def _take(self, task_id: str, kind: str) -> bool:
        with self._lock:
            key = f"{task_id}:{kind}"
            if self.specs.get(task_id) == kind and key not in self._fired:
                self._fired.add(key)
                return True
            return False

    def maybe_raise(self, task: Task) -> None:
        if self._take(task.id, "transient"):
            raise InjectedTransientError(f"injected transient failure in {task.id} (simulated LLM timeout)")

    def should_corrupt(self, task: Task) -> bool:
        return self._take(task.id, "bug")


@dataclass
class RunContext:
    run_dir: Path
    blackboard: Blackboard
    events: EventLog
    workspace: Workspace
    llm: LLMProvider
    gate: Gatekeeper
    budgets: Budgets
    faults: FaultInjector
    repo: Path | None = None
