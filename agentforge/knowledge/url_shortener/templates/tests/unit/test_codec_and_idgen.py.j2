import random

import pytest

from shortener.codec import ALPHABET, decode, encode
from shortener.idgen import CodeGenerator


@pytest.mark.parametrize("number", [0, 1, 61, 62, 3843, 3844, 2**40, 2**63 - 1])
def test_base62_round_trip(number):
    assert decode(encode(number)) == number


def test_encode_rejects_negative():
    with pytest.raises(ValueError):
        encode(-1)


@pytest.mark.parametrize("bad", ["", "abc$", "a b"])
def test_decode_rejects_invalid(bad):
    with pytest.raises(ValueError):
        decode(bad)


def test_generated_codes_have_expected_length_and_alphabet():
    gen = CodeGenerator(length=7, rng=random.Random(42))
    codes = [gen.generate() for _ in range(500)]
    assert all(len(c) == 7 and set(c) <= set(ALPHABET) for c in codes)
    assert len(set(codes)) == len(codes)


def test_generator_rejects_too_short_codes():
    with pytest.raises(ValueError):
        CodeGenerator(length=3)
