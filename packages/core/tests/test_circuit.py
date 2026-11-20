from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tokn_core.circuit import BreakerState, CircuitBreaker

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)


def breaker(threshold: int = 3, reset: timedelta = timedelta(seconds=30)) -> CircuitBreaker:
    return CircuitBreaker(failure_threshold=threshold, reset_timeout=reset)


def test_a_new_breaker_passes_traffic() -> None:
    circuit = breaker()
    assert circuit.state(NOW) is BreakerState.CLOSED
    assert circuit.allows(NOW)


def test_it_opens_after_the_threshold() -> None:
    circuit = breaker(threshold=3)
    circuit.record_failure(NOW)
    circuit.record_failure(NOW)
    assert circuit.allows(NOW), "two failures is not three"

    circuit.record_failure(NOW)
    assert circuit.state(NOW) is BreakerState.OPEN
    assert not circuit.allows(NOW)


def test_a_success_resets_the_count() -> None:
    circuit = breaker(threshold=3)
    circuit.record_failure(NOW)
    circuit.record_failure(NOW)
    circuit.record_success()

    circuit.record_failure(NOW)
    assert circuit.state(NOW) is BreakerState.CLOSED


def test_it_half_opens_after_the_reset_window() -> None:
    circuit = breaker(threshold=1, reset=timedelta(seconds=30))
    circuit.record_failure(NOW)

    assert not circuit.allows(NOW + timedelta(seconds=29))
    assert circuit.state(NOW + timedelta(seconds=30)) is BreakerState.HALF_OPEN
    assert circuit.allows(NOW + timedelta(seconds=30))


def test_only_one_probe_is_admitted() -> None:
    circuit = breaker(threshold=1, reset=timedelta(seconds=1))
    circuit.record_failure(NOW)
    later = NOW + timedelta(seconds=2)

    assert circuit.allows(later)
    assert not circuit.allows(later), "a second concurrent probe would defeat the point"


def test_a_successful_probe_closes_it() -> None:
    circuit = breaker(threshold=1, reset=timedelta(seconds=1))
    circuit.record_failure(NOW)
    later = NOW + timedelta(seconds=2)
    assert circuit.allows(later)

    circuit.record_success()
    assert circuit.state(later) is BreakerState.CLOSED
    assert circuit.allows(later)


def test_a_failed_probe_reopens_it_for_another_window() -> None:
    circuit = breaker(threshold=1, reset=timedelta(seconds=10))
    circuit.record_failure(NOW)
    later = NOW + timedelta(seconds=10)
    assert circuit.allows(later)

    circuit.record_failure(later)
    assert circuit.state(later) is BreakerState.OPEN
    assert not circuit.allows(later + timedelta(seconds=9))
    assert circuit.state(later + timedelta(seconds=10)) is BreakerState.HALF_OPEN


def test_a_nonsense_threshold_is_refused() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        CircuitBreaker(failure_threshold=0)
