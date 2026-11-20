"""Ports and the authentication step for API keys.

Lookup order is cache then database, because a 30 second cache removes a database round
trip from every request. Correctness does not depend on the cache: the presented key is
always compared against the stored digest in constant time, so a poisoned or stale cache
entry cannot authorise a wrong key.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from tokn_core.api_keys import KeyRecord, matches, parse_prefix
from tokn_core.auth.ports import Clock
from tokn_core.errors import AuthenticationFailedError

CACHE_TTL_SECONDS = 30


class KeyStore(Protocol):
    async def by_prefix(self, prefix: str) -> KeyRecord | None: ...

    async def touch_last_used(self, key_id: str, now: datetime) -> None: ...

    async def record_spend(self, key_id: str, amount_micro: int) -> None: ...

    async def revoke(self, key_id: str, now: datetime) -> None: ...


class KeyCache(Protocol):
    """A cache we can afford to lose. Never the source of truth."""

    async def get(self, prefix: str) -> KeyRecord | None: ...

    async def put(self, record: KeyRecord, ttl_seconds: int) -> None: ...

    async def invalidate(self, prefix: str) -> None: ...


async def authenticate(
    *,
    presented: str,
    store: KeyStore,
    cache: KeyCache,
    pepper: str,
    clock: Clock,
    product: str = "tokn",
) -> KeyRecord:
    """Resolve a presented key or raise one undifferentiated failure.

    Every rejection — malformed, unknown, revoked, expired, paused, over cap — raises
    ``AuthenticationFailedError``. Callers must map them all to the same response.
    """
    prefix = parse_prefix(presented, product=product)
    if prefix is None:
        raise AuthenticationFailedError("malformed key")

    record = await cache.get(prefix)
    if record is None:
        record = await store.by_prefix(prefix)
        if record is None:
            raise AuthenticationFailedError("unknown key")
        await cache.put(record, CACHE_TTL_SECONDS)

    if not matches(presented, record, pepper):
        raise AuthenticationFailedError("digest mismatch")
    if not record.is_usable(clock.now()):
        raise AuthenticationFailedError("key not usable")
    return record


async def revoke_key(
    *, prefix: str, key_id: str, store: KeyStore, cache: KeyCache, clock: Clock
) -> None:
    """Revocation is effective immediately: the cache entry goes first, then the row."""
    await cache.invalidate(prefix)
    await store.revoke(key_id, clock.now())
