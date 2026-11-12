import pytest

from shortener.ratelimit import TokenBucketLimiter


class Tick:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_bucket_allows_burst_then_limits_then_refills():
    tick = Tick()
    limiter = TokenBucketLimiter(per_minute=3, clock=tick)
    assert [limiter.allow("ip") for _ in range(4)] == [True, True, True, False]
    tick.t = 20.0  # one token refills every 20s at 3/min
    assert limiter.allow("ip") is True
    assert limiter.allow("ip") is False


def test_clients_are_isolated():
    limiter = TokenBucketLimiter(per_minute=1)
    assert limiter.allow("a") and limiter.allow("b")
    assert not limiter.allow("a")


def test_invalid_rate():
    with pytest.raises(ValueError):
        TokenBucketLimiter(per_minute=0)
