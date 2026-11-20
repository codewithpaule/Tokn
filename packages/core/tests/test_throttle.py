from __future__ import annotations

from datetime import UTC, datetime, timedelta

from hypothesis import given
from hypothesis import strategies as st

from tokn_core.auth.throttle import (
    MAX_DELAY,
    RESET_WINDOW,
    ThrottleState,
    backoff_delay,
)

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)


def test_no_failures_means_no_wait() -> None:
    assert backoff_delay(0) == timedelta(0)
    assert not ThrottleState().is_locked(NOW)


def test_the_wait_doubles_from_one_second() -> None:
    assert backoff_delay(1) == timedelta(seconds=1)
    assert backoff_delay(2) == timedelta(seconds=2)
    assert backoff_delay(3) == timedelta(seconds=4)
    assert backoff_delay(4) == timedelta(seconds=8)


def test_the_wait_is_capped_at_fifteen_minutes() -> None:
    assert backoff_delay(50) == MAX_DELAY
    assert backoff_delay(10_000) == MAX_DELAY


@given(st.integers(0, 500), st.integers(0, 500))
def test_the_wait_never_decreases_as_failures_accumulate(first: int, second: int) -> None:
    low, high = min(first, second), max(first, second)
    assert backoff_delay(low) <= backoff_delay(high) <= MAX_DELAY


def test_failures_accumulate() -> None:
    state = ThrottleState().with_failure(NOW)
    state = state.with_failure(NOW + timedelta(seconds=30))
    assert state.failures == 2
    assert state.is_locked(NOW + timedelta(seconds=31))


def test_the_counter_resets_after_an_hour_without_failures() -> None:
    state = ThrottleState().with_failure(NOW)
    later = NOW + RESET_WINDOW
    assert state.with_failure(later).failures == 1


def test_the_lock_expires_on_its_own() -> None:
    state = ThrottleState().with_failure(NOW)
    assert state.is_locked(NOW)
    assert not state.is_locked(NOW + timedelta(seconds=1, microseconds=1))


def test_retry_after_counts_down_to_zero() -> None:
    state = ThrottleState(failures=3, last_failure_at=NOW)  # four second wait
    assert state.retry_after(NOW) == timedelta(seconds=4)
    assert state.retry_after(NOW + timedelta(seconds=2)) == timedelta(seconds=2)
    assert state.retry_after(NOW + timedelta(seconds=4)) == timedelta(0)


def test_the_lock_ends_at_the_cap_and_the_counter_resets_at_the_window() -> None:
    state = ThrottleState(failures=1_000, last_failure_at=NOW)
    assert state.is_locked(NOW + MAX_DELAY - timedelta(seconds=1))
    assert not state.is_locked(NOW + MAX_DELAY)
    assert state.with_failure(NOW + RESET_WINDOW).failures == 1
    assert state.retry_after(NOW + RESET_WINDOW) == timedelta(0)


def test_clearing_removes_the_record() -> None:
    assert ThrottleState(failures=5, last_failure_at=NOW).cleared() == ThrottleState()
