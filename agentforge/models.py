"""Core data model shared by agents, the orchestrator and the reporter.

Everything here is a plain dataclass so that run state can be checkpointed to
JSON and resumed, and so that the system has no heavy runtime dependencies.
"""
from __future__ import annotations

import enum
import time
from dataclasses import asdict, dataclass, field
from typing import Any


class RequirementType(str, enum.Enum):
    GREENFIELD = "greenfield"
    BROWNFIELD = "brownfield"
    TESTS_DOCS = "tests_docs"


class Severity(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"  # blocking: needs a human answer before the plan can be trusted


@dataclass
class Ambiguity:
    id: str
    question: str
    why_it_matters: str
    options: list[str]
    default: str
    severity: Severity = Severity.MEDIUM
    resolution: str | None = None
    resolved_by: str | None = None  # "human" | "default" | "answers-file"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["severity"] = self.severity.value
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Ambiguity":
        d = dict(d)
        d["severity"] = Severity(d.get("severity", "medium"))
        return cls(**d)


@dataclass
class Spec:
    """The normalized engineering problem produced by the Requirement Analyst."""

    raw: str
    req_type: RequirementType
    domain: str
    title: str
    intent: str
    functional: list[str]
    non_functional: list[str]
    out_of_scope: list[str]
    ambiguities: list[Ambiguity]
    assumptions: list[str] = field(default_factory=list)
    options: dict[str, Any] = field(default_factory=dict)  # resolved design knobs
    acceptance_criteria: list[str] = field(default_factory=list)

    @property
    def blocking_ambiguities(self) -> list[Ambiguity]:
        return [a for a in self.ambiguities if a.severity == Severity.HIGH and a.resolution is None]

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["req_type"] = self.req_type.value
        d["ambiguities"] = [a.to_dict() for a in self.ambiguities]
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Spec":
        d = dict(d)
        d["req_type"] = RequirementType(d["req_type"])
        d["ambiguities"] = [Ambiguity.from_dict(a) for a in d.get("ambiguities", [])]
        return cls(**d)


class TaskStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    BLOCKED = "blocked"  # an upstream dependency failed
    SKIPPED = "skipped"


@dataclass
class Task:
    id: str
    title: str
    agent: str
    description: str = ""
    depends_on: list[str] = field(default_factory=list)
    params: dict[str, Any] = field(default_factory=dict)
    outputs: list[str] = field(default_factory=list)  # blackboard keys this task must publish
    max_attempts: int = 3
    critical: bool = True  # if False, failure does not block dependents
    status: TaskStatus = TaskStatus.PENDING
    attempts: int = 0
    error: str | None = None
    started_at: float | None = None
    finished_at: float | None = None
    spawned_by: str | None = None

    @property
    def duration(self) -> float | None:
        if self.started_at and self.finished_at:
            return round(self.finished_at - self.started_at, 3)
        return None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Task":
        d = dict(d)
        d["status"] = TaskStatus(d["status"])
        return cls(**d)


@dataclass
class AgentResult:
    """What an agent hands back to the orchestrator.

    * outputs  -> merged into the shared blackboard (cross-step coordination)
    * files    -> written into the sandboxed workspace (path -> content)
    * edits    -> targeted search/replace edits for brownfield changes
    * spawn    -> new tasks to add to the graph at runtime (e.g. repair loops)
    * handoff  -> id of a spawned task that downstream dependents should wait on
    """

    outputs: dict[str, Any] = field(default_factory=dict)
    files: dict[str, str] = field(default_factory=dict)
    edits: list[dict[str, str]] = field(default_factory=list)
    spawn: list[Task] = field(default_factory=list)
    handoff: str | None = None
    notes: list[str] = field(default_factory=list)


@dataclass
class CheckResult:
    name: str
    passed: bool
    details: str = ""
    blocking: bool = True
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class ValidationReport:
    iteration: int
    checks: list[CheckResult]
    timestamp: float = field(default_factory=time.time)

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks if c.blocking)

    def failures(self) -> list[CheckResult]:
        return [c for c in self.checks if not c.passed and c.blocking]

    def to_dict(self) -> dict[str, Any]:
        return {
            "iteration": self.iteration,
            "passed": self.passed,
            "timestamp": self.timestamp,
            "checks": [asdict(c) for c in self.checks],
        }


@dataclass
class Risk:
    title: str
    category: str  # scalability | security | reliability | data | delivery | process
    likelihood: str
    impact: str
    mitigation: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
