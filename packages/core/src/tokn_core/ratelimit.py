"""Token-bucket rate limiting and concurrency accounting.

Time is the only input besides the bucket itself, so the whole thing is a pure function
of (bucket, now) and can be tested without a server or a sleep. Counters live in Redis in
production, which is allowed: losing a rate-limit counter costs us a burst of traffic, not
a naira.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

MICRO = 1_000_000


@dataclass(frozen=True, slots=True)
class TokenBucket:
    """``tokens_micro`` holds fractional tokens so a slow refill is still exact."""

    tokens_micro: int
    updated_at: datetime

    @classmethod
    def full(cls, capacity: int, now: datetime) -> TokenBucket:
        return cls(tokens_micro=capacity * MICRO, updated_at=now)

    def _refilled(self, now: datetime, *, capacity: int, refill_per_second: int) -> int:
        elapsed_us = max(0, int((now - self.updated_at).total_seconds() * MICRO))
        grown = self.tokens_micro + elapsed_us * refill_per_second
        return min(grown, capacity * MICRO)

    def take(
        self,
        now: datetime,
        *,
        capacity: int,
        refill_per_second: int,
        cost: int = 1,
    ) -> tuple[TokenBucket, bool]:
        """Spend ``cost`` tokens if available. Returns the new bucket and whether it fit."""
        if capacity < 1 or refill_per_second < 0 or cost < 1:
            raise ValueError("capacity and cost must be positive; refill must not be negative")
        available = self._refilled(now, capacity=capacity, refill_per_second=refill_per_second)
        needed = cost * MICRO
        if available < needed:
            return TokenBucket(tokens_micro=available, updated_at=now), False
        return TokenBucket(tokens_micro=available - needed, updated_at=now), True

    def retry_after_seconds(self, now: datetime, *, capacity: int, refill_per_second: int) -> int:
        """Whole seconds until the bucket is full again. Zero when it already is."""
        available = self._refilled(now, capacity=capacity, refill_per_second=refill_per_second)
        missing = capacity * MICRO - available
        if missing <= 0 or refill_per_second == 0:
            return 0
        return -(-missing // (refill_per_second * MICRO))


@dataclass(frozen=True, slots=True)
class LimitPolicy:
    """How many tokens, and how fast they come back."""

    capacity: int
    refill_per_second: int

    def __post_init__(self) -> None:
        if self.capacity < 1:
            raise ValueError("capacity must be at least 1")
        if self.refill_per_second < 0:
            raise ValueError("refill must not be negative")


@dataclass(frozen=True, slots=True)
class ConcurrencyLease:
    """A slot held for the life of one request. Release it in a finally block."""

    key: str
    granted: bool
