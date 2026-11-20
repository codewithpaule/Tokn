"""Login throttling.

Failures are counted per account and per source address. The wait doubles with each
failure, from one second up to fifteen minutes, and resets after an hour without one.
The delay is a pure function of the failure count so it can be tested without a clock.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

BASE_DELAY = timedelta(seconds=1)
MAX_DELAY = timedelta(minutes=15)
RESET_WINDOW = timedelta(hours=1)
_MAX_EXPONENT = 20


def backoff_delay(failures: int) -> timedelta:
    """How long to wait after ``failures`` consecutive failures."""
    if failures <= 0:
        return timedelta(0)
    exponent = min(failures - 1, _MAX_EXPONENT)
    seconds = min(BASE_DELAY.total_seconds() * (2**exponent), MAX_DELAY.total_seconds())
    return timedelta(seconds=seconds)


@dataclass(frozen=True, slots=True)
class ThrottleState:
    failures: int = 0
    last_failure_at: datetime | None = None

    def with_failure(self, now: datetime) -> ThrottleState:
        if self.last_failure_at is not None and now - self.last_failure_at >= RESET_WINDOW:
            return ThrottleState(failures=1, last_failure_at=now)
        return ThrottleState(failures=self.failures + 1, last_failure_at=now)

    def cleared(self) -> ThrottleState:
        return ThrottleState()

    def retry_after(self, now: datetime) -> timedelta:
        """Zero when a new attempt may proceed now."""
        if self.failures <= 0 or self.last_failure_at is None:
            return timedelta(0)
        if now - self.last_failure_at >= RESET_WINDOW:
            return timedelta(0)
        remaining = backoff_delay(self.failures) - (now - self.last_failure_at)
        return max(remaining, timedelta(0))

    def is_locked(self, now: datetime) -> bool:
        return self.retry_after(now) > timedelta(0)


def is_over_limit(state: ThrottleState, now: datetime, *, limit: int) -> bool:
    """True once ``limit`` attempts have been made and the backoff is still running.

    Login uses a limit of one: every failure costs a wait. Endpoints that are merely
    rate-limited, such as password reset, use a larger limit so a normal person is not
    punished for asking twice.
    """
    return state.failures >= limit and state.is_locked(now)
