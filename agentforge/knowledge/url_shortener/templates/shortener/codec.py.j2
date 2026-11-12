"""Base62 encoding used for short codes (URL-safe, case-sensitive, compact)."""
from __future__ import annotations

ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
BASE = len(ALPHABET)
_INDEX = {ch: i for i, ch in enumerate(ALPHABET)}


def encode(number: int) -> str:
    if number < 0:
        raise ValueError("cannot encode negative numbers")
    if number == 0:
        return ALPHABET[0]
    out = []
    while number:
        number, rem = divmod(number, BASE)
        out.append(ALPHABET[rem])
    return "".join(reversed(out))


def decode(text: str) -> int:
    if not text:
        raise ValueError("cannot decode empty string")
    number = 0
    for ch in text:
        try:
            number = number * BASE + _INDEX[ch]
        except KeyError:
            raise ValueError(f"invalid base62 character: {ch!r}") from None
    return number
