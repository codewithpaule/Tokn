"""Gateway authentication over real HTTP: 401, 403, 429 and instant revocation."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from tokn_core.api_keys import KeyRecord, generate_key
from tokn_core.key_auth import revoke_key
from tokn_core.ratelimit import LimitPolicy
from tokn_gateway.auth import GatewayAuth
from tokn_infra.memory.key_store import (
    InMemoryKeyCache,
    InMemoryKeyStore,
    InMemoryRateLimiter,
)

PEPPER = "test-pepper-not-a-real-secret"
NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
BASE = "https://gateway.test"


class FrozenClock:
    def __init__(self, at: datetime = NOW) -> None:
        self._at = at

    def now(self) -> datetime:
        return self._at

    def advance(self, **kwargs: float) -> None:
        self._at += timedelta(**kwargs)


def build(
    *,
    key_capacity: int = 100,
    refill: int = 0,
    change: dict[str, object] | None = None,
) -> tuple[FastAPI, InMemoryKeyStore, InMemoryKeyCache, FrozenClock, str, KeyRecord]:
    clock = FrozenClock()
    store = InMemoryKeyStore()
    cache = InMemoryKeyCache(clock)
    limiter = InMemoryRateLimiter()
    key = generate_key(pepper=PEPPER)
    record = KeyRecord(
        id="key_1",
        account_id="acc_1",
        prefix=key.prefix,
        digest=key.digest,
        scopes=frozenset({"infer"}),
        concurrency_limit=2,
    )
    if change:
        record = replace(record, **change)  # type: ignore[arg-type]
    store.add(record)

    auth = GatewayAuth(
        store=store,
        cache=cache,
        limiter=limiter,
        clock=clock,
        pepper=PEPPER,
        key_policy=LimitPolicy(capacity=key_capacity, refill_per_second=refill),
        account_policy=LimitPolicy(capacity=10_000, refill_per_second=0),
    )

    app = FastAPI(docs_url=None, openapi_url=None)
    app.state.limiter = limiter
    app.state.record = record

    @app.get("/protected")
    async def protected(record: KeyRecord = Depends(auth)) -> dict[str, str]:
        return {"key_id": record.id}

    @app.get("/slot")
    async def slot(record: KeyRecord = Depends(auth)) -> dict[str, str]:
        async with auth.slot(record):
            return {"key_id": record.id}

    return app, store, cache, clock, key.raw, record


async def call(app: FastAPI, headers: dict[str, str] | None = None, path: str = "/protected"):
    async with AsyncClient(transport=ASGITransport(app=app), base_url=BASE) as client:
        return await client.get(path, headers=headers or {})


async def test_a_bearer_key_is_accepted() -> None:
    app, _, _, _, raw, record = build()
    response = await call(app, {"authorization": f"Bearer {raw}"})
    assert response.status_code == 200
    assert response.json() == {"key_id": record.id}


async def test_an_x_api_key_header_is_accepted() -> None:
    app, _, _, _, raw, _ = build()
    response = await call(app, {"x-api-key": raw})
    assert response.status_code == 200


async def test_no_credentials_gets_a_generic_401() -> None:
    app, _, _, _, _, _ = build()
    response = await call(app)
    assert response.status_code == 401
    assert response.json()["detail"] == "invalid api key"


@pytest.mark.parametrize(
    "headers",
    [
        {"authorization": "Bearer tokn_live_" + "0" * 64},
        {"authorization": "Bearer garbage"},
        {"authorization": "Basic abc"},
        {"x-api-key": "tokn_live_short"},
    ],
)
async def test_every_bad_credential_looks_the_same(headers: dict[str, str]) -> None:
    app, _, _, _, _, _ = build()
    response = await call(app, headers)
    assert response.status_code == 401
    assert response.json()["detail"] == "invalid api key"


async def test_contradictory_headers_are_refused() -> None:
    app, _, _, _, raw, _ = build()
    other = generate_key(pepper=PEPPER).raw
    response = await call(app, {"authorization": f"Bearer {raw}", "x-api-key": other})
    assert response.status_code == 401


async def test_a_key_without_the_infer_scope_is_forbidden() -> None:
    app, _, _, _, raw, _ = build(change={"scopes": frozenset({"read"})})
    response = await call(app, {"authorization": f"Bearer {raw}"})
    assert response.status_code == 403
    assert "scope" in response.json()["detail"]


async def test_an_ip_outside_the_allowlist_is_forbidden() -> None:
    app, _, _, _, raw, _ = build(change={"ip_allowlist": ("203.0.113.9",)})
    allowed = await call(app, {"authorization": f"Bearer {raw}", "x-forwarded-for": "203.0.113.9"})
    refused = await call(app, {"authorization": f"Bearer {raw}", "x-forwarded-for": "198.51.100.1"})

    assert allowed.status_code == 200
    assert refused.status_code == 403


async def test_the_rate_limit_returns_429_with_retry_after() -> None:
    app, _, _, _, raw, _ = build(key_capacity=2)
    headers = {"authorization": f"Bearer {raw}"}

    assert (await call(app, headers)).status_code == 200
    assert (await call(app, headers)).status_code == 200

    limited = await call(app, headers)
    assert limited.status_code == 429
    assert limited.json()["detail"] == "rate limit exceeded"


async def test_revocation_is_immediate_even_with_a_warm_cache() -> None:
    app, store, cache, clock, raw, record = build()
    headers = {"authorization": f"Bearer {raw}"}
    assert (await call(app, headers)).status_code == 200  # primes the cache

    await revoke_key(prefix=record.prefix, key_id=record.id, store=store, cache=cache, clock=clock)

    assert (await call(app, headers)).status_code == 401


async def test_an_expired_key_is_rejected_after_the_clock_moves() -> None:
    app, _, _, clock, raw, _ = build(change={"expires_at": NOW + timedelta(minutes=5)})
    headers = {"authorization": f"Bearer {raw}"}

    assert (await call(app, headers)).status_code == 200
    clock.advance(minutes=6)
    assert (await call(app, headers)).status_code == 401


async def test_the_concurrency_slot_is_a_real_cap() -> None:
    app, _, _, _, raw, _ = build()
    limiter = app.state.limiter
    headers = {"authorization": f"Bearer {raw}"}

    # Sequential requests each release their slot, so all three pass.
    for _ in range(3):
        assert (await call(app, headers, path="/slot")).status_code == 200

    # Two requests still in flight fill the cap of two.
    assert await limiter.acquire("concurrency:key_1", limit=2)
    assert await limiter.acquire("concurrency:key_1", limit=2)

    refused = await call(app, headers, path="/slot")
    assert refused.status_code == 429
    assert "concurrent" in refused.json()["detail"]

    await limiter.release("concurrency:key_1")
    assert (await call(app, headers, path="/slot")).status_code == 200
