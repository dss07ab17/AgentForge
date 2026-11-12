"""LLM provider abstraction.

Agents ask for *structured JSON* with an explicit shape. Every agent has a
deterministic offline strategy and an LLM strategy; which one runs depends on
``provider.is_live``. Output from an LLM is always parsed and validated by the
calling agent, and invalid output is retried with the error fed back.
"""
from __future__ import annotations

import json
import re
import threading
from abc import ABC, abstractmethod
from typing import Any


class LLMError(RuntimeError):
    pass


class LLMBudgetExceeded(LLMError):
    pass


class LLMProvider(ABC):
    name = "base"
    is_live = False

    def __init__(self, max_calls: int = 120) -> None:
        self.max_calls = max_calls
        self.calls = 0
        self._lock = threading.Lock()

    def _count(self) -> None:
        with self._lock:
            if self.calls >= self.max_calls:
                raise LLMBudgetExceeded(f"LLM call budget of {self.max_calls} exhausted")
            self.calls += 1

    def complete_json(self, system: str, prompt: str, *, shape: str, max_tokens: int = 8000) -> Any:
        self._count()
        text = self._complete(system, f"{prompt}\n\nRespond with ONLY valid JSON matching this shape:\n{shape}",
                              max_tokens=max_tokens)
        return parse_json(text)

    @abstractmethod
    def _complete(self, system: str, prompt: str, *, max_tokens: int) -> str: ...


def parse_json(text: str) -> Any:
    """Parse JSON from a model reply, tolerating ```json fences and prose around it."""
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    candidate = fenced.group(1) if fenced else text
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        start = min((i for i in (candidate.find("{"), candidate.find("[")) if i >= 0), default=-1)
        end = max(candidate.rfind("}"), candidate.rfind("]"))
        if start >= 0 and end > start:
            try:
                return json.loads(candidate[start:end + 1])
            except json.JSONDecodeError as exc:
                raise LLMError(f"model returned invalid JSON: {exc}") from exc
        raise LLMError("model reply contained no JSON")


class OfflineProvider(LLMProvider):
    """Marker provider: agents use their deterministic strategies (no network)."""

    name = "offline"
    is_live = False

    def _complete(self, system: str, prompt: str, *, max_tokens: int) -> str:  # pragma: no cover
        raise LLMError("offline provider does not generate text; agents use deterministic strategies")
