"""Input validation for target URLs and custom aliases."""
from __future__ import annotations

import re
from urllib.parse import urlsplit

from .errors import InvalidRequest

ALLOWED_SCHEMES = {"http", "https"}
_CONTROL_CHARS = re.compile(r"[\x00-\x20\x7f]")
ALIAS_PATTERN = re.compile(r"^[A-Za-z0-9_-]{3,32}$")
RESERVED_ALIASES = {"api", "healthz", "docs", "static", "admin", "login", "favicon.ico"}


def validate_url(url: object, max_length: int = 2048) -> str:
    if not isinstance(url, str) or not url.strip():
        raise InvalidRequest("'url' must be a non-empty string")
    url = url.strip()
    if len(url) > max_length:
        raise InvalidRequest(f"'url' must be at most {max_length} characters")
    if _CONTROL_CHARS.search(url):
        raise InvalidRequest("'url' must not contain whitespace or control characters")
    parts = urlsplit(url)
    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        raise InvalidRequest("'url' must use http or https")
    if not parts.hostname:
        raise InvalidRequest("'url' must include a host")
    return url


def validate_alias(alias: object) -> str:
    if not isinstance(alias, str) or not ALIAS_PATTERN.fullmatch(alias):
        raise InvalidRequest("'custom_alias' must be 3-32 characters of letters, digits, '-' or '_'")
    if alias.lower() in RESERVED_ALIASES:
        raise InvalidRequest(f"'custom_alias' {alias!r} is reserved")
    return alias
