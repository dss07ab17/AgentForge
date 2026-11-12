"""Thread-safe LRU cache with per-entry TTL for hot redirect lookups.

Redirects are read-heavy (typically 100:1 reads to writes); caching resolved
links keeps the hot path off the database. In a multi-instance deployment this
is replaced by (or layered in front of) a shared cache such as Redis.
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Callable, Generic, TypeVar

V = TypeVar("V")


class TTLCache(Generic[V]):
    def __init__(self, max_size: int = 10_000, ttl_seconds: float = 300.0,
                 clock: Callable[[], float] = time.monotonic) -> None:
        if max_size <= 0:
            raise ValueError("max_size must be positive")
        self.max_size = max_size
        self.ttl = ttl_seconds
        self._clock = clock
        self._data: OrderedDict[str, tuple[float, V]] = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> V | None:
        with self._lock:
            item = self._data.get(key)
            if item is None:
                self.misses += 1
                return None
            expires_at, value = item
            if self._clock() >= expires_at:
                del self._data[key]
                self.misses += 1
                return None
            self._data.move_to_end(key)
            self.hits += 1
            return value

    def set(self, key: str, value: V) -> None:
        with self._lock:
            self._data[key] = (self._clock() + self.ttl, value)
            self._data.move_to_end(key)
            while len(self._data) > self.max_size:
                self._data.popitem(last=False)

    def delete(self, key: str) -> None:
        with self._lock:
            self._data.pop(key, None)

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)
