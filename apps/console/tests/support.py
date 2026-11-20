"""Shared harness: a console wired to an in-memory store and a frozen clock.

The password hasher is deliberately cheap here so the suite stays fast; Argon2 itself is
tested directly in ``test_argon2.py``. Everything else is the production wiring.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI, Response
from httpx import ASGITransport, AsyncClient

from tokn_console.app import create_app
from tokn_console.config import Settings
from tokn_console.web import CSRF_COOKIE
from tokn_core.auth.totp import code_at
from tokn_infra.memory.auth_store import InMemoryAuthStore

BASE = "https://console.test"
STRONG = "a-long-passphrase-2026"
ROTATED = "a-brand-new-passphrase"
EMAIL = "engineer@tokn.ng"


class FrozenClock:
    def __init__(self, at: datetime | None = None) -> None:
        self._at = at or datetime(2026, 11, 20, 12, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self._at

    def advance(self, **kwargs: float) -> None:
        self._at += timedelta(**kwargs)


class FastHasher:
    """Not Argon2. Only for keeping the HTTP tests quick."""

    def hash(self, password: str) -> str:
        return "fast$" + hashlib.sha256(password.encode()).hexdigest()

    def verify(self, password: str, hashed: str) -> bool:
        return hmac.compare_digest(self.hash(password), hashed)

    def needs_rehash(self, hashed: str) -> bool:
        return False


@dataclass
class Harness:
    app: FastAPI
    store: InMemoryAuthStore
    clock: FrozenClock
    client: AsyncClient


@pytest.fixture
async def harness() -> AsyncIterator[Harness]:
    store = InMemoryAuthStore()
    clock = FrozenClock()
    app = create_app(
        Settings(env="development", secure_cookies=True, console_url=BASE),
        store=store,
        hasher=FastHasher(),
        clock=clock,
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url=BASE) as client:
        yield Harness(app=app, store=store, clock=clock, client=client)


async def csrf_token(client: AsyncClient, path: str = "/login") -> str:
    existing = client.cookies.get(CSRF_COOKIE)
    if existing:
        return existing
    await client.get(path)
    return client.cookies.get(CSRF_COOKIE) or ""


async def form_post(client: AsyncClient, path: str, data: dict[str, str] | None = None) -> Response:
    token = await csrf_token(client)
    payload = {**(data or {}), "csrf_token": token}
    return await client.post(path, data=payload, follow_redirects=False)


def last_link(store: InMemoryAuthStore) -> str:
    body = store.emails[-1][2]
    match = re.search(r"(https://console\.test/\S+)", body)
    assert match is not None, f"no link in email: {body}"
    return match.group(1)


async def sign_up(
    harness: Harness,
    email: str = EMAIL,
    password: str = STRONG,
    *,
    verify: bool = True,
) -> str:
    await form_post(harness.client, "/signup", {"email": email, "password": password})
    if verify:
        await harness.client.get(last_link(harness.store))
    return email


async def log_in(
    harness: Harness, email: str = EMAIL, password: str = STRONG, code: str = ""
) -> Response:
    return await form_post(
        harness.client, "/login", {"email": email, "password": password, "code": code}
    )


async def log_out(harness: Harness) -> Response:
    return await form_post(harness.client, "/logout")


async def enable_two_factor(harness: Harness, email: str = EMAIL) -> tuple[str, list[str]]:
    response = await log_in(harness, email)
    assert response.status_code == 303, response.text
    page = await harness.client.get("/app/2fa")
    secret = re.search(r'name="secret" value="([A-Za-z0-9]+)"', page.text)
    assert secret is not None, page.text
    value = secret.group(1)
    result = await form_post(
        harness.client,
        "/app/2fa",
        {"secret": value, "code": code_at(value, harness.clock.now())},
    )
    codes = re.findall(r'<li class="mono">([A-Z0-9]{4}-[A-Z0-9]{4})</li>', result.text)
    return value, codes
