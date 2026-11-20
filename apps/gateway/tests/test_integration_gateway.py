"""End to end through the whole stack, with only the model provider mocked.

A real API key row, the real catalogue, the real wallet, the real usage sink — and an
upstream response served from memory. If this passes, the only thing between the code and
a paying customer is a provider key.

Skips unless ``TOKN_TEST_DATABASE_URL`` is set.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tokn_core.api_keys import generate_key
from tokn_core.money import Naira
from tokn_core.pricing import PriceSchedule
from tokn_core.ratelimit import LimitPolicy
from tokn_gateway import create_app
from tokn_gateway.auth import GatewayAuth
from tokn_gateway.proxy import Proxy
from tokn_infra.clock import SystemClock
from tokn_infra.db.models import (
    Account,
    ApiKey,
    LedgerEntry,
    ModelCatalog,
    ModelPrice,
    Wallet,
)
from tokn_infra.db.models import (
    UsageEvent as UsageEventRow,
)
from tokn_infra.memory.key_store import InMemoryKeyCache, InMemoryRateLimiter
from tokn_infra.postgres import PostgresKeyStore, PostgresWalletStore
from tokn_infra.postgres.catalogue import PostgresCatalogue, PostgresUsageSink

DATABASE_URL = os.environ.get("TOKN_TEST_DATABASE_URL")
PEPPER = "integration-pepper-not-a-real-secret"
BASE = "https://gateway.test"
NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)

IN_PRICE = 240_000_000  # 240 naira per million input tokens
OUT_PRICE = 960_000_000
MARKUP_BPS = 400

SSE_CHUNKS = [
    b'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n',
    b'data: {"choices":[{"delta":{"content":" from Tokn"}}]}\n\n',
    b'data: {"choices":[],"usage":{"prompt_tokens":1500,"completion_tokens":600}}\n\n',
    b"data: [DONE]\n\n",
]

pytestmark = [
    pytest.mark.skipif(
        not DATABASE_URL, reason="set TOKN_TEST_DATABASE_URL to run integration tests"
    ),
    pytest.mark.asyncio(loop_scope="module"),
]


class Upstream:
    """A provider that answers from memory, in the same SSE shape OpenAI uses."""

    def __init__(self) -> None:
        self.payloads: list[dict[str, object]] = []

    async def open(self, payload: dict[str, object]) -> httpx.Response:
        self.payloads.append(payload)
        return httpx.Response(200, stream=_Chunks(SSE_CHUNKS))


class _Chunks(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def __aiter__(self):
        for chunk in self._chunks:
            yield chunk


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def factory():
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL, pool_size=4, max_overflow=4, pool_pre_ping=True)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "TRUNCATE usage_events, reservations, ledger_entries, api_keys, "
                "wallets, accounts, model_prices, model_catalog CASCADE"
            )
        )
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def world(factory):
    """An account with a funded wallet, one live key, and one priced model."""
    account_id = uuid.uuid4()
    key_id = uuid.uuid4()
    key = generate_key(pepper=PEPPER)

    async with factory() as session, session.begin():
        session.add(
            Account(
                id=account_id,
                email=f"gateway-{account_id.hex[:8]}@tokn.ng",
                password_hash="hashed",  # noqa: S106 - a placeholder in a test fixture
                status="active",
                email_verified_at=NOW,
            )
        )
        await session.flush()
        session.add(Wallet(account_id=account_id, balance_micro=Naira.from_naira("1000").micro))
        session.add(
            ApiKey(
                id=key_id,
                account_id=account_id,
                prefix=key.prefix,
                key_hash=bytes.fromhex(key.digest),
                name="end to end",
                scopes=["infer"],
            )
        )
        model = ModelCatalog(
            alias="tokn-test",
            provider="mock",
            upstream_model="mock-upstream-1",
            protocol="openai",
            enabled=True,
            max_context_tokens=128_000,
            max_output_tokens=4096,
        )
        session.add(model)
        await session.flush()
        session.add(
            ModelPrice(
                catalog_id=model.id,
                input_per_million_micro=IN_PRICE,
                output_per_million_micro=OUT_PRICE,
                markup_bps=MARKUP_BPS,
                effective_from=NOW - timedelta(days=1),
            )
        )
        session.add(
            LedgerEntry(
                account_id=account_id,
                kind="topup",
                amount_micro=Naira.from_naira("1000").micro,
                balance_after_micro=Naira.from_naira("1000").micro,
                reference=f"topup-{account_id}",
            )
        )

    return {"account_id": account_id, "key_id": key_id, "raw": key.raw}


def build_gateway(factory, upstream: Upstream) -> httpx.ASGITransport:
    clock = SystemClock()
    key_store = PostgresKeyStore(factory)
    proxy = Proxy(
        wallet=PostgresWalletStore(factory),
        usage_sink=PostgresUsageSink(factory),
        catalogue=PostgresCatalogue(factory),
        adapters={"mock": upstream},
        clock=clock,
        key_store=key_store,
    )
    auth = GatewayAuth(
        store=key_store,
        cache=InMemoryKeyCache(clock),
        limiter=InMemoryRateLimiter(),
        clock=clock,
        pepper=PEPPER,
        key_policy=LimitPolicy(capacity=100, refill_per_second=10),
        account_policy=LimitPolicy(capacity=100, refill_per_second=10),
    )
    return httpx.ASGITransport(app=create_app(proxy=proxy, auth=auth))


async def test_a_request_is_authenticated_streamed_settled_and_receipted(world, factory) -> None:
    upstream = Upstream()
    transport = build_gateway(factory, upstream)

    async with httpx.AsyncClient(transport=transport, base_url=BASE, timeout=30) as client:
        response = await client.post(
            "/v1/chat/completions",
            json={
                "model": "tokn-test",
                "messages": [{"role": "user", "content": "x" * 3000}],
                "max_tokens": 800,
                "stream": True,
            },
            headers={"authorization": f"Bearer {world['raw']}"},
        )

    assert response.status_code == 200
    assert b"Hello" in response.content
    assert upstream.payloads[0]["model"] == "mock-upstream-1", "the alias was rewritten"

    expected = PriceSchedule(
        input_per_million=Naira(IN_PRICE),
        output_per_million=Naira(OUT_PRICE),
        markup_bps=MARKUP_BPS,
    ).charge(_usage(1500, 600))

    async with factory() as session:
        receipt = await session.scalar(select(UsageEventRow))
        balance = await session.scalar(
            select(Wallet.balance_micro).where(Wallet.account_id == world["account_id"])
        )
        key_row = await session.get(ApiKey, world["key_id"])
        ledger_total = await session.scalar(
            select(func.sum(LedgerEntry.amount_micro)).where(
                LedgerEntry.account_id == world["account_id"]
            )
        )

    assert receipt is not None
    assert receipt.status == "ok"
    assert receipt.model_alias == "tokn-test"
    assert receipt.upstream_model == "mock-upstream-1"
    assert receipt.input_tokens == 1500
    assert receipt.output_tokens == 600
    assert receipt.charged_micro == expected.micro
    assert receipt.flagged_for_review is False

    assert balance == Naira.from_naira("1000").micro - expected.micro
    assert key_row is not None
    assert key_row.spent_micro == expected.micro
    assert key_row.last_used_at is not None
    assert ledger_total == balance, "the ledger still explains the balance"


async def test_a_revoked_key_is_refused_end_to_end(world, factory) -> None:
    upstream = Upstream()
    transport = build_gateway(factory, upstream)

    async with factory() as session, session.begin():
        await session.execute(
            text("UPDATE api_keys SET revoked_at = now() WHERE id = :id"),
            {"id": world["key_id"]},
        )

    async with httpx.AsyncClient(transport=transport, base_url=BASE, timeout=30) as client:
        response = await client.post(
            "/v1/chat/completions",
            json={"model": "tokn-test", "messages": [], "max_tokens": 10},
            headers={"authorization": f"Bearer {world['raw']}"},
        )

    assert response.status_code == 401
    assert upstream.payloads == [], "nothing reached the provider"

    async with factory() as session, session.begin():
        await session.execute(
            text("UPDATE api_keys SET revoked_at = NULL WHERE id = :id"),
            {"id": world["key_id"]},
        )


def _usage(input_tokens: int, output_tokens: int):
    from tokn_core.pricing import TokenUsage

    return TokenUsage(input_tokens=input_tokens, output_tokens=output_tokens)
