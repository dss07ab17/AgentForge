"""Application service: the business rules, independent of HTTP and storage details."""
from __future__ import annotations

from datetime import timedelta
from typing import Callable

from .analytics import ClickRecorder
from .cache import TTLCache
from .config import Settings
from .errors import Conflict, DuplicateCode, Gone, InvalidRequest, NotFound, Unavailable
from .idgen import CodeGenerator
from .models import ClickEvent, Link, utcnow
from .validation import validate_alias, validate_url
from urllib.parse import urlsplit

MAX_CODE_ATTEMPTS = 5


class ShortenerService:
    def __init__(self, repo, settings: Settings, *, cache: TTLCache | None = None,
                 recorder: ClickRecorder | None = None, codegen: CodeGenerator | None = None,
                 clock: Callable = utcnow) -> None:
        self.repo = repo
        self.settings = settings
        self.cache = cache if cache is not None else TTLCache(settings.cache_size, settings.cache_ttl_seconds)
        self.recorder = recorder if recorder is not None else ClickRecorder(repo)
        self.codegen = codegen or CodeGenerator(settings.code_length)
        self.clock = clock

    # ------------------------------------------------------------- commands
    def create_link(self, url: object, custom_alias: object = None, expires_in_seconds: object = None) -> Link:
        target = validate_url(url, self.settings.max_url_length)
        now = self.clock()
        expires_at = None
        if expires_in_seconds is not None:
            if isinstance(expires_in_seconds, bool) or not isinstance(expires_in_seconds, int):
                raise InvalidRequest("'expires_in_seconds' must be an integer")
            lo, hi = self.settings.min_ttl_seconds, self.settings.max_ttl_seconds
            if not lo <= expires_in_seconds <= hi:
                raise InvalidRequest(f"'expires_in_seconds' must be between {lo} and {hi}")
            expires_at = now + timedelta(seconds=expires_in_seconds)
        if custom_alias is not None:
            alias = validate_alias(custom_alias)
            link = Link(code=alias, target_url=target, created_at=now, is_custom=True, expires_at=expires_at)
            try:
                self.repo.insert_link(link)
            except DuplicateCode:
                raise Conflict(f"alias {alias!r} is already taken") from None
            return link
        for _ in range(MAX_CODE_ATTEMPTS):
            link = Link(code=self.codegen.generate(), target_url=target, created_at=now, expires_at=expires_at)
            try:
                self.repo.insert_link(link)
                return link
            except DuplicateCode:
                continue  # astronomically rare at default length; retry with a fresh code
        raise Unavailable("could not allocate a unique short code, please retry")

    def delete_link(self, code: str) -> None:
        if not self.repo.delete_link(code):
            raise NotFound(f"no link with code {code!r}")
        self.cache.delete(code)

    # --------------------------------------------------------------- queries
    def get_link(self, code: str) -> Link:
        link = self.cache.get(code)
        if link is None:
            link = self.repo.get_link(code)
            if link is None:
                raise NotFound(f"no link with code {code!r}")
            self.cache.set(code, link)
        return link

    def resolve(self, code: str, *, referrer: str | None = None, user_agent: str | None = None) -> str:
        """Hot path: look up the target for a redirect and record the click asynchronously."""
        link = self.get_link(code)
        now = self.clock()
        if link.is_expired(now):
            raise Gone(f"link {code!r} has expired")
        ref_host = urlsplit(referrer).hostname if referrer else None
        self.recorder.record(ClickEvent(code=code, occurred_at=now, referrer_host=ref_host, user_agent=user_agent))
        return link.target_url

    def get_stats(self, code: str) -> dict:
        self.get_link(code)  # 404 for unknown codes
        stats = self.repo.stats(code)
        stats["pending_events"] = self.recorder.pending()  # analytics is eventually consistent
        return stats

    def health(self) -> dict:
        return {"status": "ok" if self.repo.ping() else "degraded",
                "analytics_dropped": self.recorder.dropped, "cache_entries": len(self.cache)}
