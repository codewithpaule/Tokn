"""Per-provider circuit breaker.

Three states. Closed passes traffic. Open passes nothing until the reset window elapses.
Half-open admits a single probe request: success closes the breaker, failure re-opens it
for another window. Nothing here does I/O, so the transitions can be tested exactly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

DEFAULT_THRESHOLD = 5
DEFAULT_RESET = timedelta(seconds=30)


class BreakerState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass(slots=True)
class CircuitBreaker:
    failure_threshold: int = DEFAULT_THRESHOLD
    reset_timeout: timedelta = DEFAULT_RESET
    failures: int = 0
    opened_at: datetime | None = None
    probe_in_flight: bool = False

    def __post_init__(self) -> None:
        if self.failure_threshold < 1:
            raise ValueError("failure_threshold must be at least 1")

    def state(self, now: datetime) -> BreakerState:
        if self.opened_at is None:
            return BreakerState.CLOSED
        if now - self.opened_at >= self.reset_timeout:
            return BreakerState.HALF_OPEN
        return BreakerState.OPEN

    def allows(self, now: datetime) -> bool:
        """Whether a request may be sent to this provider right now."""
        current = self.state(now)
        if current is BreakerState.CLOSED:
            return True
        if current is BreakerState.OPEN:
            return False
        if self.probe_in_flight:
            return False  # exactly one probe at a time
        self.probe_in_flight = True
        return True

    def record_success(self) -> None:
        self.failures = 0
        self.opened_at = None
        self.probe_in_flight = False

    def record_failure(self, now: datetime) -> None:
        self.probe_in_flight = False
        if self.state(now) is BreakerState.HALF_OPEN:
            self._open(now)
            return
        self.failures += 1
        if self.failures >= self.failure_threshold:
            self._open(now)

    def _open(self, now: datetime) -> None:
        self.opened_at = now
        self.failures = self.failure_threshold
