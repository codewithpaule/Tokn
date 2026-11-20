"""Key authentication: cache, constant-time comparison, and instant revocation."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from tokn_core.api_keys import KeyRecord, generate_key
from tokn_core.errors import AuthenticationFailedError
from tokn_core.key_auth import (
    CACHE_TTL_SECONDS,
    KeyIssuanceRefusedError,
    NewKeyRequest,
    authenticate,
    issue_key,
    revoke_key,
)
from tokn_infra.memory.key_store import InMemoryKeyCache, InMemoryKeyStore

PEPPER = "test-pepper-not-a-real-secret"
NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)


class FrozenClock:
    def __init__(self, at: datetime = NOW) -> None:
        self._at = at

    def now(self) -> datetime:
        return self._at

    def advance(self, **kwargs: float) -> None:
        self._at += timedelta(**kwargs)


def setup() -> tuple[InMemoryKeyStore, InMemoryKeyCache, FrozenClock, KeyRecord, str]:
    clock = FrozenClock()
    store = InMemoryKeyStore()
    cache = InMemoryKeyCache(clock)
    key = generate_key(pepper=PEPPER)
    record = KeyRecord(
        id="key_1",
        account_id="acc_1",
        prefix=key.prefix,
        digest=key.digest,
        scopes=frozenset({"infer"}),
    )
    store.add(record)
    return store, cache, clock, record, key.raw


async def authenticate_raw(
    raw: str, store: InMemoryKeyStore, cache: InMemoryKeyCache, clock: FrozenClock
) -> KeyRecord:
    return await authenticate(presented=raw, store=store, cache=cache, pepper=PEPPER, clock=clock)


async def test_a_valid_key_authenticates_and_is_cached() -> None:
    store, cache, clock, record, raw = setup()

    first = await authenticate_raw(raw, store, cache, clock)
    second = await authenticate_raw(raw, store, cache, clock)

    assert first.id == second.id == record.id
    assert cache.hits == 1
    assert cache.misses == 1, "the second call came from the cache"


@pytest.mark.parametrize(
    "candidate",
    [
        "",
        "not-a-key",
        "tokn_live_" + "0" * 64,  # well-shaped, unknown prefix
    ],
)
async def test_bad_keys_fail_with_one_undifferentiated_error(candidate: str) -> None:
    store, cache, clock, _, _ = setup()
    with pytest.raises(AuthenticationFailedError):
        await authenticate_raw(candidate, store, cache, clock)


async def test_a_valid_prefix_with_the_wrong_secret_fails() -> None:
    store, cache, clock, _, raw = setup()
    forged = raw[:-4] + "dead"
    with pytest.raises(AuthenticationFailedError):
        await authenticate_raw(forged, store, cache, clock)


@pytest.mark.parametrize(
    "change",
    [
        {"revoked_at": NOW},
        {"expires_at": NOW},
        {"auto_paused": True},
        {"spend_cap_micro": 1_000, "spent_micro": 1_000},
    ],
)
async def test_unusable_keys_fail_identically(change: dict[str, object]) -> None:
    store, cache, clock, record, raw = setup()
    store.add(replace(record, **change))  # type: ignore[arg-type]

    with pytest.raises(AuthenticationFailedError):
        await authenticate_raw(raw, store, cache, clock)


async def test_revocation_is_effective_immediately_despite_the_cache() -> None:
    store, cache, clock, record, raw = setup()
    await authenticate_raw(raw, store, cache, clock)  # primes the cache
    assert cache.hits == 0

    await revoke_key(prefix=record.prefix, key_id=record.id, store=store, cache=cache, clock=clock)

    with pytest.raises(AuthenticationFailedError):
        await authenticate_raw(raw, store, cache, clock)


async def test_a_stale_cache_entry_cannot_resurrect_a_revoked_key() -> None:
    """Revoking behind the cache's back still fails once the cached copy expires."""
    store, cache, clock, record, raw = setup()
    await authenticate_raw(raw, store, cache, clock)
    await store.revoke(record.id, clock.now())  # no cache invalidation on purpose

    clock.advance(seconds=CACHE_TTL_SECONDS + 1)
    with pytest.raises(AuthenticationFailedError):
        await authenticate_raw(raw, store, cache, clock)


async def test_the_cache_ttl_is_thirty_seconds() -> None:
    assert CACHE_TTL_SECONDS == 30


async def test_a_poisoned_cache_entry_cannot_authorise_the_wrong_key() -> None:
    store, cache, clock, record, raw = setup()
    other = generate_key(pepper=PEPPER)
    await cache.put(
        replace(record, digest=other.digest), CACHE_TTL_SECONDS
    )  # attacker-controlled entry

    with pytest.raises(AuthenticationFailedError):
        await authenticate_raw(raw, store, cache, clock)


@pytest.mark.parametrize(
    "candidate",
    [
        NewKeyRequest(account_id="acc_1", name="   "),
        NewKeyRequest(account_id="acc_1", name="ok", spend_cap_micro=-1),
        NewKeyRequest(account_id="acc_1", name="ok", scopes=frozenset()),
        NewKeyRequest(account_id="acc_1", name="ok", concurrency_limit=0),
    ],
)
async def test_impossible_key_requests_are_refused(candidate: NewKeyRequest) -> None:
    store, _, _, _, _ = setup()
    with pytest.raises(KeyIssuanceRefusedError):
        await issue_key(
            request=candidate,
            store=store,
            pepper=PEPPER,
            product="tokn",
            environment="live",
            new_id="key_new",
        )


async def test_a_refused_request_leaves_no_key_behind() -> None:
    store, _, _, _, _ = setup()
    with pytest.raises(KeyIssuanceRefusedError):
        await issue_key(
            request=NewKeyRequest(account_id="acc_1", name=""),
            store=store,
            pepper=PEPPER,
            product="tokn",
            environment="live",
            new_id="key_new",
        )
    assert await store.by_id("key_new") is None
