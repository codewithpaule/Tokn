"""Integration tests against a real PostgreSQL database.

These skip unless ``TOKN_TEST_DATABASE_URL`` points at a disposable database. They are
the first thing to run when one exists: ``TOKN_TEST_DATABASE_URL=postgresql+asyncpg://...
pytest packages/infra/tests/test_integration_postgres.py``.
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from tokn_core.api_keys import KeyRecord, generate_key
from tokn_core.auth.throttle import ThrottleState
from tokn_core.auth.tokens import TokenKind
from tokn_core.errors import AccountExistsError
from tokn_infra.memory.key_store import InMemoryKeyCache
from tokn_infra.postgres.auth_store import PostgresAuthStore
from tokn_infra.postgres.key_store import PostgresKeyStore
from tokn_infra.postgres.session import create_engine, create_session_factory

DATABASE_URL = os.environ.get("TOKN_TEST_DATABASE_URL")
PEPPER = "integration-pepper-not-a-real-secret"
NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="set TOKN_TEST_DATABASE_URL to run integration tests"
)


class MemoryThrottle:
    def __init__(self) -> None:
        self._states: dict[str, ThrottleState] = {}

    async def throttle(self, identity: str) -> ThrottleState:
        return self._states.get(identity, ThrottleState())

    async def save_throttle(self, identity: str, state: ThrottleState) -> None:
        self._states[identity] = state


@pytest.fixture(scope="module")
def factory():
    assert DATABASE_URL is not None
    engine = create_engine(DATABASE_URL)

    async def reset() -> None:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "TRUNCATE recovery_codes, auth_tokens, sessions, ledger_entries, "
                    "reservations, usage_events, api_keys, wallets, accounts CASCADE"
                )
            )
        await engine.dispose()

    asyncio.run(reset())
    return create_session_factory(engine)


@pytest.fixture
def auth_store(factory):
    return PostgresAuthStore(factory, MemoryThrottle())


@pytest.fixture
def key_store(factory):
    return PostgresKeyStore(factory)


async def test_accounts_round_trip_and_email_is_unique(auth_store) -> None:
    account = await auth_store.create_account("engineer@tokn.ng", "hashed")
    assert account.email == "engineer@tokn.ng"
    assert account.email_verified is False

    fetched = await auth_store.account_by_email("engineer@tokn.ng")
    assert fetched is not None
    assert fetched.id == account.id

    with pytest.raises(AccountExistsError):
        await auth_store.create_account("engineer@tokn.ng", "other")


async def test_verifying_activates_the_account(auth_store) -> None:
    account = await auth_store.create_account("verify@tokn.ng", "hashed")
    await auth_store.set_email_verified(account.id, NOW)

    fetched = await auth_store.account_by_id(account.id)
    assert fetched is not None
    assert fetched.email_verified
    assert fetched.status == "active"


async def test_a_token_is_consumed_exactly_once(auth_store) -> None:
    account = await auth_store.create_account("token@tokn.ng", "hashed")
    await auth_store.issue_token(account.id, "digest-1", TokenKind.PASSWORD_RESET, NOW)

    assert await auth_store.consume_token("digest-1", TokenKind.PASSWORD_RESET, NOW) == account.id
    assert await auth_store.consume_token("digest-1", TokenKind.PASSWORD_RESET, NOW) is None


async def test_an_expired_token_is_not_consumed(auth_store) -> None:
    account = await auth_store.create_account("expired@tokn.ng", "hashed")
    await auth_store.issue_token(account.id, "digest-2", TokenKind.EMAIL_VERIFICATION, NOW)

    later = NOW + timedelta(minutes=31)
    assert await auth_store.consume_token("digest-2", TokenKind.EMAIL_VERIFICATION, later) is None


async def test_a_recovery_code_is_burned_once(auth_store) -> None:
    account = await auth_store.create_account("codes@tokn.ng", "hashed")
    await auth_store.set_recovery_codes(account.id, ("code-a", "code-b"))

    assert await auth_store.consume_recovery_code(account.id, "code-a") is True
    assert await auth_store.consume_recovery_code(account.id, "code-a") is False
    assert await auth_store.consume_recovery_code(account.id, "code-b") is True


async def test_sessions_are_created_touched_and_deleted(auth_store) -> None:
    account = await auth_store.create_account("session@tokn.ng", "hashed")
    await auth_store.create_session("sess-1", account.id, NOW, "203.0.113.9", "pytest")

    stored = await auth_store.session("sess-1")
    assert stored is not None
    assert stored.account_id == account.id

    await auth_store.touch_session("sess-1", NOW + timedelta(minutes=5))
    touched = await auth_store.session("sess-1")
    assert touched is not None
    assert touched.last_seen_at > NOW

    await auth_store.delete_session("sess-1")
    assert await auth_store.session("sess-1") is None


async def test_keys_are_stored_hashed_and_listed(key_store, auth_store) -> None:
    account = await auth_store.create_account("keys@tokn.ng", "hashed")
    key = generate_key(pepper=PEPPER)
    record = KeyRecord(
        id="11111111-1111-1111-1111-111111111111",
        account_id=account.id,
        prefix=key.prefix,
        digest=key.digest,
        scopes=frozenset({"infer"}),
        name="laptop",
    )
    await key_store.create(record)

    fetched = await key_store.by_prefix(key.prefix)
    assert fetched is not None
    assert fetched.digest == key.digest
    assert fetched.name == "laptop"
    assert await key_store.by_prefix("tokn_live_zzzzzz") is None

    listed = await key_store.list_for_account(account.id)
    assert [k.prefix for k in listed] == [key.prefix]

    await key_store.revoke(record.id, NOW)
    revoked = await key_store.by_prefix(key.prefix)
    assert revoked is not None
    assert revoked.revoked_at is not None


async def test_spend_accumulates(key_store, auth_store) -> None:
    account = await auth_store.create_account("spend@tokn.ng", "hashed")
    key = generate_key(pepper=PEPPER)
    record = KeyRecord(
        id="22222222-2222-2222-2222-222222222222",
        account_id=account.id,
        prefix=key.prefix,
        digest=key.digest,
        scopes=frozenset({"infer"}),
    )
    await key_store.create(record)
    await key_store.record_spend(record.id, 500)
    await key_store.record_spend(record.id, 250)

    fetched = await key_store.by_id(record.id)
    assert fetched is not None
    assert fetched.spent_micro == 750


async def test_the_cache_sits_in_front_of_the_store(key_store, auth_store) -> None:
    account = await auth_store.create_account("cache@tokn.ng", "hashed")
    key = generate_key(pepper=PEPPER)
    await key_store.create(
        KeyRecord(
            id="33333333-3333-3333-3333-333333333333",
            account_id=account.id,
            prefix=key.prefix,
            digest=key.digest,
            scopes=frozenset({"infer"}),
        )
    )
    cache = InMemoryKeyCache(clock=_Frozen())
    from tokn_core.key_auth import authenticate

    first = await authenticate(
        presented=key.raw, store=key_store, cache=cache, pepper=PEPPER, clock=_Frozen()
    )
    second = await authenticate(
        presented=key.raw, store=key_store, cache=cache, pepper=PEPPER, clock=_Frozen()
    )
    assert first.id == second.id
    assert cache.hits == 1


class _Frozen:
    def now(self) -> datetime:
        return NOW
