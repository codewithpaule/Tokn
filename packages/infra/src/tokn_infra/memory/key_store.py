"""In-memory key store, cache and rate limiter.

Same contracts as the Redis and PostgreSQL adapters, without the servers. Used by the
gateway tests and by local development.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta

from tokn_core.api_keys import KeyRecord
from tokn_core.auth.ports import Clock
from tokn_core.ratelimit import LimitPolicy, TokenBucket


class InMemoryKeyStore:
    def __init__(self) -> None:
        self._by_prefix: dict[str, KeyRecord] = {}
        self._by_id: dict[str, KeyRecord] = {}
        self.last_used_at: datetime | None = None

    def add(self, record: KeyRecord) -> None:
        self._by_prefix[record.prefix] = record
        self._by_id[record.id] = record

    async def by_prefix(self, prefix: str) -> KeyRecord | None:
        return self._by_prefix.get(prefix)

    def _update(self, key_id: str, **changes: object) -> None:
        record = self._by_id[key_id]
        updated = replace(record, **changes)  # type: ignore[arg-type]
        self._by_id[key_id] = updated
        self._by_prefix[record.prefix] = updated

    async def touch_last_used(self, key_id: str, now: datetime) -> None:
        self.last_used_at = now

    async def record_spend(self, key_id: str, amount_micro: int) -> None:
        self._update(key_id, spent_micro=self._by_id[key_id].spent_micro + amount_micro)

    async def revoke(self, key_id: str, now: datetime) -> None:
        self._update(key_id, revoked_at=now)


class InMemoryKeyCache:
    """Honest about being a cache: entries carry an expiry and are simply dropped."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._entries: dict[str, tuple[KeyRecord, datetime]] = {}
        self.hits = 0
        self.misses = 0

    async def get(self, prefix: str) -> KeyRecord | None:
        entry = self._entries.get(prefix)
        if entry is None:
            self.misses += 1
            return None
        record, expires_at = entry
        if self._clock.now() >= expires_at:
            del self._entries[prefix]
            self.misses += 1
            return None
        self.hits += 1
        return record

    async def put(self, record: KeyRecord, ttl_seconds: int) -> None:
        self._entries[record.prefix] = (
            record,
            self._clock.now() + timedelta(seconds=ttl_seconds),
        )

    async def invalidate(self, prefix: str) -> None:
        self._entries.pop(prefix, None)


class InMemoryRateLimiter:
    def __init__(self) -> None:
        self._buckets: dict[str, TokenBucket] = {}
        self._leases: dict[str, int] = {}

    async def consume(
        self, key: str, *, policy: LimitPolicy, now: datetime, cost: int = 1
    ) -> tuple[bool, int]:
        bucket = self._buckets.get(key, TokenBucket.full(policy.capacity, now))
        updated, allowed = bucket.take(
            now,
            capacity=policy.capacity,
            refill_per_second=policy.refill_per_second,
            cost=cost,
        )
        self._buckets[key] = updated
        retry = (
            0
            if allowed
            else updated.retry_after_seconds(
                now, capacity=policy.capacity, refill_per_second=policy.refill_per_second
            )
        )
        return allowed, retry

    async def acquire(self, key: str, *, limit: int) -> bool:
        current = self._leases.get(key, 0)
        if current >= limit:
            return False
        self._leases[key] = current + 1
        return True

    async def release(self, key: str) -> None:
        self._leases[key] = max(0, self._leases.get(key, 0) - 1)
