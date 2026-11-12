"""Human-in-the-loop approval gates (controlled autonomy).

Agents run on their own between gates; humans decide at the points where a
wrong guess is expensive:

  G1 clarify  - answer blocking ambiguities before anything is planned
  G2 plan     - approve the normalized spec + task graph before execution
  G3 changes  - approve the validated diff/artifacts before they are released

Modes:
  interactive - prompt on the terminal
  auto        - approve by policy; blocking ambiguities STOP the run unless
                --accept-defaults is given (the system never silently guesses
                on something it rated high-impact)
  answers     - read decisions from a JSON file (reproducible / CI friendly),
                falling back to the auto policy for anything not listed
Every decision is recorded in an audit trail that ends up in the report.
"""
from __future__ import annotations

import json
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .models import Ambiguity, Severity


class ClarificationNeeded(Exception):
    def __init__(self, questions: list[Ambiguity]) -> None:
        super().__init__(f"{len(questions)} blocking question(s) need a human answer")
        self.questions = questions


class GateRejected(Exception):
    pass


@dataclass
class Gatekeeper:
    mode: str = "auto"  # interactive | auto | answers
    answers: dict[str, Any] = field(default_factory=dict)
    accept_defaults: bool = False
    audit: list[dict[str, Any]] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @classmethod
    def build(cls, mode: str, answers_file: Path | None, accept_defaults: bool) -> "Gatekeeper":
        answers = json.loads(answers_file.read_text(encoding="utf-8")) if answers_file else {}
        if answers_file and mode == "auto":
            mode = "answers"
        return cls(mode=mode, answers=answers, accept_defaults=accept_defaults)

    def _record(self, gate: str, decision: str, by: str, comment: str = "") -> None:
        with self._lock:
            self.audit.append({"gate": gate, "decision": decision, "by": by, "comment": comment, "ts": time.time()})

    # ------------------------------------------------------------ G1 clarify
    def clarify(self, ambiguities: list[Ambiguity]) -> list[Ambiguity]:
        provided = self.answers.get("clarifications", {})
        unresolved: list[Ambiguity] = []
        for amb in ambiguities:
            if amb.resolution is not None:
                continue
            if amb.id in provided:
                amb.resolution, amb.resolved_by = str(provided[amb.id]), "answers-file"
            elif self.mode == "interactive" and amb.severity != Severity.LOW:
                amb.resolution, amb.resolved_by = self._ask(amb), "human"
            elif amb.severity != Severity.HIGH or self.accept_defaults:
                amb.resolution, amb.resolved_by = amb.default, "default"
            else:
                unresolved.append(amb)
                continue
            self._record(f"clarify:{amb.id}", amb.resolution, amb.resolved_by)
        if unresolved:
            self._record("clarify", "halted", "policy", f"{len(unresolved)} blocking question(s) unanswered")
            raise ClarificationNeeded(unresolved)
        return ambiguities

    def _ask(self, amb: Ambiguity) -> str:
        print(f"\n[clarify:{amb.severity.value}] {amb.question}\n  why: {amb.why_it_matters}", file=sys.stderr)
        for i, opt in enumerate(amb.options, 1):
            print(f"   {i}. {opt}{'  (default)' if opt == amb.default else ''}", file=sys.stderr)
        raw = input("  answer [number, text, or Enter for default]: ").strip()
        if not raw:
            return amb.default
        if raw.isdigit() and 1 <= int(raw) <= len(amb.options):
            return amb.options[int(raw) - 1]
        return raw

    # ---------------------------------------------------------- G2/G3 approve
    def approve(self, gate: str, summary: str) -> None:
        provided = self.answers.get("approvals", {})
        if gate in provided:
            ok = bool(provided[gate])
            self._record(gate, "approved" if ok else "rejected", "answers-file")
        elif self.mode == "interactive":
            print(f"\n=== APPROVAL GATE: {gate} ===\n{summary}\n", file=sys.stderr)
            reply = input("Approve? [y/N/comment]: ").strip()
            ok = reply.lower() in {"y", "yes"}
            self._record(gate, "approved" if ok else "rejected", "human", "" if ok else reply)
        else:
            ok = True
            self._record(gate, "approved", "auto-policy", "non-interactive run; approval delegated to policy")
        if not ok:
            raise GateRejected(f"human rejected gate '{gate}'")
