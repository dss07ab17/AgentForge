"""Per-client token-bucket rate limiter for the write API.

In-process only: with several instances behind a load balancer each instance
enforces its own budget; a shared store (e.g. Redis) is needed for a global limit.
"""
from __future__ import annotations

import threading
import time
from typing import Callable


class TokenBucketLimiter:
    def __init__(self, per_minute: int, clock: Callable[[], float] = time.monotonic,
                 max_clients: int = 100_000) -> None:
        if per_minute <= 0:
            raise ValueError("per_minute must be positive")
        self.capacity = float(per_minute)
        self.refill_per_second = per_minute / 60.0
        self._clock = clock
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()
        self._max_clients = max_clients

    def allow(self, key: str) -> bool:
        now = self._clock()
        with self._lock:
            tokens, last = self._buckets.get(key, (self.capacity, now))
            tokens = min(self.capacity, tokens + (now - last) * self.refill_per_second)
            allowed = tokens >= 1.0
            self._buckets[key] = (tokens - 1.0 if allowed else tokens, now)
            if len(self._buckets) > self._max_clients:  # bound memory: drop the oldest-seen client
                oldest = min(self._buckets, key=lambda k: self._buckets[k][1])
                del self._buckets[oldest]
            return allowed

    def retry_after(self) -> int:
        return max(1, int(round(1 / self.refill_per_second)))
