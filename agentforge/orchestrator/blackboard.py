"""Shared, thread-safe state that agents use to coordinate across steps.

Agents never call each other directly. A task publishes named facts (the API
contract, the impact analysis, the latest validation report, ...) and any later
task can read them. Every write is versioned and attributed so the final report
can show *which* step produced *which* decision.
"""
from __future__ import annotations

import copy
import threading
import time
from typing import Any


class Blackboard:
    def __init__(self, initial: dict[str, Any] | None = None) -> None:
        self._lock = threading.RLock()
        self._data: dict[str, Any] = dict(initial or {})
        self._history: list[dict[str, Any]] = []

    def put(self, key: str, value: Any, *, author: str) -> None:
        with self._lock:
            version = sum(1 for h in self._history if h["key"] == key) + 1
            self._data[key] = value
            self._history.append({"key": key, "author": author, "version": version, "ts": time.time()})

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            value = self._data.get(key, default)
            return copy.deepcopy(value)

    def require(self, key: str) -> Any:
        with self._lock:
            if key not in self._data:
                raise KeyError(f"blackboard is missing required key '{key}' (upstream step did not publish it)")
            return copy.deepcopy(self._data[key])

    def has(self, key: str) -> bool:
        with self._lock:
            return key in self._data

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._data)

    def history(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._history)

    def restore(self, data: dict[str, Any], history: list[dict[str, Any]]) -> None:
        with self._lock:
            self._data = dict(data)
            self._history = list(history)
