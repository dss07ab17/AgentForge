"""Runtime configuration, loaded from environment variables (12-factor style)."""
from __future__ import annotations

import os
from dataclasses import dataclass, fields


@dataclass(frozen=True)
class Settings:
    database_path: str = "shortener.db"
    base_url: str = "http://localhost:8080"
    code_length: int = 7
    max_url_length: int = 2048
    max_body_bytes: int = 16_384
    cache_size: int = 10_000
    cache_ttl_seconds: float = 300.0
    analytics_flush_interval: float = 0.5
    analytics_batch_size: int = 500
    analytics_queue_size: int = 100_000
    min_ttl_seconds: int = 60
    max_ttl_seconds: int = 10 * 365 * 24 * 3600
    rate_limit_per_minute: int = 60

    @classmethod
    def from_env(cls, prefix: str = "SHORTENER_") -> "Settings":
        """Build settings from ``SHORTENER_<FIELD>`` variables, casting to each field's type."""
        values = {}
        for f in fields(cls):
            raw = os.environ.get(prefix + f.name.upper())
            if raw is None:
                continue
            caster = {"int": int, "float": float}.get(str(f.type), str)
            values[f.name] = caster(raw)
        return cls(**values)
