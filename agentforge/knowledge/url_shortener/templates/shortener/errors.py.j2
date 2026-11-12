"""Domain errors. Each maps to one HTTP status and a stable machine-readable code."""
from __future__ import annotations


class ShortenerError(Exception):
    status = 500
    code = "internal_error"

    def __init__(self, message: str = "internal error") -> None:
        super().__init__(message)
        self.message = message


class InvalidRequest(ShortenerError):
    status, code = 400, "invalid_request"


class NotFound(ShortenerError):
    status, code = 404, "not_found"


class Conflict(ShortenerError):
    status, code = 409, "conflict"


class Gone(ShortenerError):
    status, code = 410, "gone"


class PayloadTooLarge(ShortenerError):
    status, code = 413, "payload_too_large"


class RateLimited(ShortenerError):
    status, code = 429, "rate_limited"

    def __init__(self, message: str = "too many requests", retry_after: int = 60) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class Unavailable(ShortenerError):
    status, code = 503, "unavailable"


class DuplicateCode(Exception):
    """Raised by storage when a short code already exists (unique constraint)."""
