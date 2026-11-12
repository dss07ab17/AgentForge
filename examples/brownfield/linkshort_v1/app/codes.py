"""Short code generation."""
import random
import string

CODE_LENGTH = 5


def new_code(rng=random):
    return "".join(rng.choice(string.ascii_lowercase) for _ in range(CODE_LENGTH))
