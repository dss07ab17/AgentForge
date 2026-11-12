from shortener.cache import TTLCache


class Tick:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_get_returns_value_until_ttl_expires():
    tick = Tick()
    cache = TTLCache(max_size=10, ttl_seconds=5, clock=tick)
    cache.set("a", 1)
    tick.t = 4.9
    assert cache.get("a") == 1
    tick.t = 5.0
    assert cache.get("a") is None
    assert cache.hits == 1 and cache.misses == 1


def test_least_recently_used_entry_is_evicted():
    cache = TTLCache(max_size=2, ttl_seconds=60)
    cache.set("a", 1)
    cache.set("b", 2)
    cache.get("a")          # a is now most recently used
    cache.set("c", 3)       # evicts b
    assert cache.get("b") is None
    assert cache.get("a") == 1 and cache.get("c") == 3
    assert len(cache) == 2


def test_delete_is_idempotent():
    cache = TTLCache(max_size=2, ttl_seconds=60)
    cache.set("a", 1)
    cache.delete("a")
    cache.delete("a")
    assert cache.get("a") is None
