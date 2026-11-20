from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tokn_core.ratelimit import LimitPolicy, TokenBucket

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
POLICY = LimitPolicy(capacity=10, refill_per_second=2)


def test_a_full_bucket_allows_exactly_its_capacity() -> None:
    bucket = TokenBucket.full(POLICY.capacity, NOW)
    allowed = 0
    for _ in range(20):
        bucket, ok = bucket.take(
            NOW, capacity=POLICY.capacity, refill_per_second=POLICY.refill_per_second
        )
        allowed += int(ok)
    assert allowed == POLICY.capacity


def test_tokens_come_back_at_the_configured_rate() -> None:
    bucket = TokenBucket.full(POLICY.capacity, NOW)
    for _ in range(10):
        bucket, _ = bucket.take(
            NOW, capacity=POLICY.capacity, refill_per_second=POLICY.refill_per_second
        )

    later = NOW + timedelta(seconds=1)
    bucket, first = bucket.take(
        later, capacity=POLICY.capacity, refill_per_second=POLICY.refill_per_second
    )
    assert first, "two tokens per second means two requests one second later"

    bucket, second = bucket.take(
        later, capacity=POLICY.capacity, refill_per_second=POLICY.refill_per_second
    )
    assert second

    _, third = bucket.take(
        later, capacity=POLICY.capacity, refill_per_second=POLICY.refill_per_second
    )
    assert not third, "the refill is two tokens, not three"


def test_the_bucket_never_refills_beyond_capacity() -> None:
    bucket = TokenBucket.full(POLICY.capacity, NOW)
    far_future = NOW + timedelta(days=30)
    refilled, _ = bucket.take(
        far_future, capacity=POLICY.capacity, refill_per_second=POLICY.refill_per_second
    )
    assert refilled.tokens_micro <= POLICY.capacity * 1_000_000


def test_a_slow_refill_is_fractional_and_exact() -> None:
    slow = LimitPolicy(capacity=1, refill_per_second=1)
    bucket = TokenBucket.full(1, NOW)
    bucket, _ = bucket.take(NOW, capacity=1, refill_per_second=1)

    half = bucket.take(NOW + timedelta(milliseconds=500), capacity=1, refill_per_second=1)
    assert not half[1], "half a token is not a token"

    whole = half[0].take(NOW + timedelta(milliseconds=1001), capacity=1, refill_per_second=1)
    assert whole[1]
    assert slow.refill_per_second == 1


def test_a_cost_larger_than_one_is_supported() -> None:
    bucket = TokenBucket.full(10, NOW)
    bucket, ok = bucket.take(NOW, capacity=10, refill_per_second=1, cost=7)
    assert ok
    bucket, ok = bucket.take(NOW, capacity=10, refill_per_second=1, cost=4)
    assert not ok, "three tokens left cannot cover four"


def test_retry_after_is_whole_seconds_to_refill() -> None:
    bucket = TokenBucket.full(10, NOW)
    empty, _ = bucket.take(NOW, capacity=10, refill_per_second=2, cost=10)
    assert empty.retry_after_seconds(NOW, capacity=10, refill_per_second=2) == 5
    assert (
        empty.retry_after_seconds(NOW + timedelta(seconds=5), capacity=10, refill_per_second=2) == 0
    )


def test_impossible_configuration_is_refused() -> None:
    with pytest.raises(ValueError, match="capacity"):
        LimitPolicy(capacity=0, refill_per_second=1)
    with pytest.raises(ValueError, match="refill"):
        LimitPolicy(capacity=1, refill_per_second=-1)
    with pytest.raises(ValueError, match="positive"):
        TokenBucket.full(1, NOW).take(NOW, capacity=1, refill_per_second=1, cost=0)


@given(
    st.lists(st.integers(0, 3000), min_size=1, max_size=40),
    st.integers(1, 50),
    st.integers(0, 20),
)
def test_time_travel_never_yields_more_than_capacity_plus_refill(
    waits: list[int], capacity: int, refill: int
) -> None:
    bucket = TokenBucket.full(capacity, NOW)
    now = NOW
    spent = 0
    for millis in waits:
        now += timedelta(milliseconds=millis)
        bucket, ok = bucket.take(now, capacity=capacity, refill_per_second=refill)
        spent += int(ok)
    elapsed_seconds = (now - NOW).total_seconds()
    assert spent <= capacity + refill * elapsed_seconds + 1
