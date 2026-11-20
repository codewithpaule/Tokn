"""Ports and the authentication step for API keys.

Lookup order is cache then database, because a 30 second cache removes a database round
trip from every request. Correctness does not depend on the cache: the presented key is
always compared against the stored digest in constant time, so a poisoned or stale cache
entry cannot authorise a wrong key.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from tokn_core.api_keys import (
    REQUIRED_SCOPE,
    GeneratedKey,
    KeyRecord,
    generate_key,
    matches,
    parse_prefix,
)
from tokn_core.auth.ports import Clock
from tokn_core.errors import AuthenticationFailedError, DomainError

CACHE_TTL_SECONDS = 30


class KeyStore(Protocol):
    async def by_prefix(self, prefix: str) -> KeyRecord | None: ...

    async def by_id(self, key_id: str) -> KeyRecord | None: ...

    async def list_for_account(self, account_id: str) -> tuple[KeyRecord, ...]: ...

    async def create(self, record: KeyRecord) -> None: ...

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


@dataclass(frozen=True, slots=True)
class NewKeyRequest:
    """What the customer asked for when they clicked "create key"."""

    account_id: str
    name: str
    scopes: frozenset[str] = frozenset({REQUIRED_SCOPE})
    spend_cap_micro: int | None = None
    expires_at: datetime | None = None
    ip_allowlist: tuple[str, ...] | None = None
    concurrency_limit: int = 8


class KeyIssuanceRefusedError(DomainError):
    """The account may not hold keys yet, or asked for something impossible."""


async def issue_key(
    *,
    request: NewKeyRequest,
    store: KeyStore,
    pepper: str,
    product: str,
    environment: str,
    new_id: str,
) -> GeneratedKey:
    """Mint a key for an account. The raw value is returned and never stored."""
    if not request.name.strip():
        raise KeyIssuanceRefusedError("give the key a name you will recognise later")
    if request.spend_cap_micro is not None and request.spend_cap_micro < 0:
        raise KeyIssuanceRefusedError("a spend cap cannot be negative")
    if not request.scopes:
        raise KeyIssuanceRefusedError("choose at least one scope")
    if request.concurrency_limit < 1:
        raise KeyIssuanceRefusedError("the concurrency limit must be at least one")

    key = generate_key(pepper=pepper, product=product, environment=environment)
    await store.create(
        KeyRecord(
            id=new_id,
            account_id=request.account_id,
            prefix=key.prefix,
            digest=key.digest,
            scopes=request.scopes,
            spend_cap_micro=request.spend_cap_micro,
            expires_at=request.expires_at,
            ip_allowlist=request.ip_allowlist,
            concurrency_limit=request.concurrency_limit,
        )
    )
    return key


async def list_keys(store: KeyStore, account_id: str) -> tuple[KeyRecord, ...]:
    return await store.list_for_account(account_id)
