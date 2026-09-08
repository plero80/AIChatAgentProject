import time

from RateLimiter import SlidingWindowLimiter


def consume(limiter, key, times):
    """Return how many of `times` attempts were allowed."""
    allowed = 0
    for _ in range(times):
        if limiter.retry_after(key) is None:
            limiter.record(key)
            allowed += 1
    return allowed


def test_allows_up_to_the_limit_then_blocks():
    limiter = SlidingWindowLimiter(max_requests=3, window_seconds=60)

    assert consume(limiter, "user", 5) == 3
    assert limiter.retry_after("user") is not None


def test_keys_are_independent():
    limiter = SlidingWindowLimiter(max_requests=1, window_seconds=60)

    consume(limiter, "user-a", 1)

    assert limiter.retry_after("user-a") is not None
    assert limiter.retry_after("user-b") is None


def test_window_slides_so_old_hits_expire():
    limiter = SlidingWindowLimiter(max_requests=2, window_seconds=0.3)

    consume(limiter, "user", 2)
    assert limiter.retry_after("user") is not None

    time.sleep(0.35)
    assert limiter.retry_after("user") is None


def test_retry_after_never_exceeds_the_window():
    limiter = SlidingWindowLimiter(max_requests=1, window_seconds=5)

    consume(limiter, "user", 1)
    retry_after = limiter.retry_after("user")

    assert 0 < retry_after <= 5


def test_idle_keys_are_pruned():
    limiter = SlidingWindowLimiter(max_requests=5, window_seconds=0.2)

    for i in range(50):
        consume(limiter, f"user-{i}", 1)

    time.sleep(0.25)
    consume(limiter, "fresh-user", 1)

    assert len(limiter._hits) == 1
