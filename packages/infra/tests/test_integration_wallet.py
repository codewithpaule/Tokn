"""The wallet against a real database: concurrency, idempotency and the ledger.

These are the tests that a fake wallet cannot replace. They skip unless
``TOKN_TEST_DATABASE_URL`` is set.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tokn_core.api_keys import KeyRecord, generate_key
from tokn_core.errors import UnknownReservationError
from tokn_core.money import Naira
from tokn_infra.db.models import LedgerEntry, Reservation, Wallet
from tokn_infra.postgres.auth_store import PostgresAuthStore
from tokn_infra.postgres.key_store import PostgresKeyStore
from tokn_infra.postgres.wallet_store import PostgresWalletStore

DATABASE_URL = os.environ.get("TOKN_TEST_DATABASE_URL")
PEPPER = "integration-pepper-not-a-real-secret"
NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)

pytestmark = [
    pytest.mark.skipif(
        not DATABASE_URL, reason="set TOKN_TEST_DATABASE_URL to run integration tests"
    ),
    pytest.mark.asyncio(loop_scope="module"),
]


class MemoryThrottle:
    async def throttle(self, identity: str):
        from tokn_core.auth.throttle import ThrottleState

        return ThrottleState()

    async def save_throttle(self, identity: str, state) -> None:
        return None


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def factory():
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL, pool_size=4, max_overflow=4, pool_pre_ping=True)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "TRUNCATE ledger_entries, reservations, usage_events, "
                "api_keys, wallets, accounts CASCADE"
            )
        )
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest_asyncio.fixture(loop_scope="module")
async def account(factory):
    """An account with a wallet, a key, and a single top-up on the ledger."""
    auth = PostgresAuthStore(factory, MemoryThrottle())
    email = f"wallet-{uuid.uuid4().hex[:8]}@tokn.ng"
    created = await auth.create_account(email, "hashed")

    wallet = PostgresWalletStore(factory)
    async with factory() as session, session.begin():
        session.add(Wallet(account_id=uuid.UUID(created.id), balance_micro=0))
    await wallet.credit(
        account_id=created.id, amount=Naira.from_naira("1000"), reference=f"topup-{created.id}"
    )

    keys = PostgresKeyStore(factory)
    key = generate_key(pepper=PEPPER)
    key_id = str(uuid.uuid4())
    await keys.create(
        KeyRecord(
            id=key_id,
            account_id=created.id,
            prefix=key.prefix,
            digest=key.digest,
            scopes=frozenset({"infer"}),
            name="test",
        )
    )
    return created.id, key_id


def wallet(factory) -> PostgresWalletStore:
    return PostgresWalletStore(factory)


async def test_a_hold_reduces_the_balance(account, factory) -> None:
    account_id, key_id = account
    store = wallet(factory)

    assert await store.reserve(
        request_id=f"req-{uuid.uuid4().hex}",
        account_id=account_id,
        key_id=key_id,
        hold=Naira.from_naira("200"),
    )
    assert await store.balance(account_id) == Naira.from_naira("800")


async def test_a_hold_beyond_the_balance_is_refused_and_changes_nothing(account, factory) -> None:
    account_id, key_id = account
    store = wallet(factory)
    before = await store.balance(account_id)

    held = await store.reserve(
        request_id=f"req-{uuid.uuid4().hex}",
        account_id=account_id,
        key_id=key_id,
        hold=Naira.from_naira("10000"),
    )

    assert held is False
    assert await store.balance(account_id) == before


async def test_settlement_refunds_the_unused_hold_and_writes_one_ledger_line(
    account, factory
) -> None:
    account_id, key_id = account
    store = wallet(factory)
    request_id = f"req-{uuid.uuid4().hex}"
    await store.reserve(
        request_id=request_id, account_id=account_id, key_id=key_id, hold=Naira.from_naira("300")
    )

    charged = await store.settle(request_id=request_id, charge=Naira.from_naira("120"))

    assert charged == Naira.from_naira("120")
    assert await store.balance(account_id) == Naira.from_naira("880")

    async with factory() as session:
        entries = list(
            await session.scalars(select(LedgerEntry).where(LedgerEntry.reference == request_id))
        )
        reservation = await session.scalar(
            select(Reservation).where(Reservation.request_id == request_id)
        )
        total = await session.scalar(
            select(func.sum(LedgerEntry.amount_micro)).where(
                LedgerEntry.account_id == uuid.UUID(account_id)
            )
        )

    assert len(entries) == 1
    assert entries[0].amount_micro == -Naira.from_naira("120").micro
    assert entries[0].balance_after_micro == Naira.from_naira("880").micro
    assert reservation is not None
    assert reservation.status == "settled"
    assert reservation.charge_micro == Naira.from_naira("120").micro
    assert total == Naira.from_naira("880").micro, "ledger sum equals the balance"


async def test_settling_twice_charges_once(account, factory) -> None:
    account_id, key_id = account
    store = wallet(factory)
    request_id = f"req-{uuid.uuid4().hex}"
    await store.reserve(
        request_id=request_id, account_id=account_id, key_id=key_id, hold=Naira.from_naira("300")
    )

    first = await store.settle(request_id=request_id, charge=Naira.from_naira("100"))
    balance_after_first = await store.balance(account_id)
    second = await store.settle(request_id=request_id, charge=Naira.from_naira("100"))

    assert first == second == Naira.from_naira("100")
    assert await store.balance(account_id) == balance_after_first
    async with factory() as session:
        count = await session.scalar(
            select(func.count()).select_from(LedgerEntry).where(LedgerEntry.reference == request_id)
        )
    assert count == 1


async def test_a_charge_above_the_hold_is_capped(account, factory) -> None:
    account_id, key_id = account
    store = wallet(factory)
    request_id = f"req-{uuid.uuid4().hex}"
    await store.reserve(
        request_id=request_id, account_id=account_id, key_id=key_id, hold=Naira.from_naira("50")
    )

    charged = await store.settle(request_id=request_id, charge=Naira.from_naira("500"))

    assert charged == Naira.from_naira("50")
    assert await store.balance(account_id) == Naira.from_naira("950")


async def test_release_returns_the_whole_hold(account, factory) -> None:
    account_id, key_id = account
    store = wallet(factory)
    request_id = f"req-{uuid.uuid4().hex}"
    before = await store.balance(account_id)
    await store.reserve(
        request_id=request_id, account_id=account_id, key_id=key_id, hold=Naira.from_naira("250")
    )

    await store.release(request_id=request_id)

    assert await store.balance(account_id) == before
    async with factory() as session:
        reservation = await session.scalar(
            select(Reservation).where(Reservation.request_id == request_id)
        )
    assert reservation is not None
    assert reservation.status == "released"


async def test_settling_an_unknown_reservation_raises(account, factory) -> None:
    store = wallet(factory)
    with pytest.raises(UnknownReservationError):
        await store.settle(request_id="never-existed", charge=Naira(1))


async def test_concurrent_holds_cannot_overspend(account, factory) -> None:
    """The whole point: twenty simultaneous holds against a balance that covers ten."""
    account_id, key_id = account
    store = wallet(factory)
    async with factory() as session, session.begin():
        await session.execute(
            text("UPDATE wallets SET balance_micro = :b WHERE account_id = :a"),
            {"b": Naira.from_naira("1000").micro, "a": uuid.UUID(account_id)},
        )

    async def attempt(index: int) -> bool:
        return await store.reserve(
            request_id=f"race-{uuid.uuid4().hex}",
            account_id=account_id,
            key_id=key_id,
            hold=Naira.from_naira("100"),
        )

    results = await asyncio.gather(*[attempt(i) for i in range(20)])

    assert sum(results) == 10, "exactly ten holds of 100 naira fit in 1000"
    assert await store.balance(account_id) == Naira.zero()
