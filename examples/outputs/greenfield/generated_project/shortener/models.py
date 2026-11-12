"""Domain models."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if value else None


def from_iso(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


@dataclass(frozen=True)
class Link:
    code: str
    target_url: str
    created_at: datetime
    is_custom: bool = False
    expires_at: datetime | None = None

    def is_expired(self, now: datetime) -> bool:
        return self.expires_at is not None and now >= self.expires_at

    def to_dict(self, base_url: str) -> dict:
        return {
            "code": self.code,
            "short_url": f"{base_url.rstrip('/')}/{self.code}",
            "target_url": self.target_url,
            "created_at": to_iso(self.created_at),
            "expires_at": to_iso(self.expires_at),
            "is_custom": self.is_custom,
        }


@dataclass(frozen=True)
class ClickEvent:
    code: str
    occurred_at: datetime
    referrer_host: str | None = None
    user_agent: str | None = None
