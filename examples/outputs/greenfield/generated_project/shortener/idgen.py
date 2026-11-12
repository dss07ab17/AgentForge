"""Short-code generation.

Strategy: cryptographically random base62 codes. With the default length of 7
there are 62**7 (about 3.5 trillion) codes, so collisions are rare; the service
still retries on a unique-constraint violation. Random codes (unlike a
sequential counter) are not enumerable and need no central coordinator, which
lets many API instances generate codes independently.
"""
from __future__ import annotations

import random
import secrets

from .codec import ALPHABET


class CodeGenerator:
    def __init__(self, length: int = 7, rng: random.Random | None = None) -> None:
        if length < 4:
            raise ValueError("code length must be at least 4")
        self.length = length
        self._rng = rng or secrets.SystemRandom()

    def generate(self) -> str:
        return "".join(self._rng.choice(ALPHABET) for _ in range(self.length))
