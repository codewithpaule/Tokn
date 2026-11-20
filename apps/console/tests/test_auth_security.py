"""Headers, cookies, CSRF, session rotation, expiry and throttling."""

from __future__ import annotations

from httpx import ASGITransport, AsyncClient
from support import (
    BASE,
    EMAIL,
    STRONG,
    FastHasher,
    FrozenClock,
    Harness,
    csrf_token,
    form_post,
    log_in,
    log_out,
    sign_up,
)

from tokn_console.app import create_app
from tokn_console.config import Settings
from tokn_console.web import CONTENT_SECURITY_POLICY
from tokn_infra.memory.auth_store import InMemoryAuthStore


async def test_every_response_carries_the_security_headers(harness: Harness) -> None:
    response = await harness.client.get("/login")
    headers = response.headers

    assert headers["content-security-policy"] == CONTENT_SECURITY_POLICY
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert "camera=()" in headers["permissions-policy"]
    assert headers["cross-origin-opener-policy"] == "same-origin"
    assert headers["cross-origin-resource-policy"] == "same-origin"
    assert headers["x-frame-options"] == "DENY"
    assert "strict-transport-security" not in headers


async def test_hsts_appears_when_https_is_enforced() -> None:
    app = create_app(
        Settings(env="development", enforce_https=True, console_url=BASE),
        store=InMemoryAuthStore(),
        hasher=FastHasher(),
        clock=FrozenClock(),
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url=BASE) as client:
        response = await client.get("/login")

    assert "includeSubDomains" in response.headers["strict-transport-security"]
    assert "preload" in response.headers["strict-transport-security"]


async def test_the_session_cookie_is_host_prefixed_and_httponly(harness: Harness) -> None:
    await sign_up(harness)
    response = await log_in(harness)

    cookie = response.headers["set-cookie"]
    assert cookie.startswith("__Host-session=")
    assert "HttpOnly" in cookie
    assert "Secure" in cookie
    assert "SameSite=lax" in cookie
    assert "Path=/" in cookie
    assert "Domain=" not in cookie


async def test_login_rotates_the_session_id(harness: Harness) -> None:
    await sign_up(harness)
    await log_in(harness)
    first = harness.client.cookies.get("__Host-session")

    await log_in(harness)
    second = harness.client.cookies.get("__Host-session")

    assert first != second, "a new id on every login defeats session fixation"
    assert len(first) >= 43
    assert len(second) >= 43


async def test_a_post_without_a_csrf_token_is_refused(harness: Harness) -> None:
    response = await harness.client.post(
        "/login", data={"email": EMAIL, "password": STRONG}, follow_redirects=False
    )
    assert response.status_code == 403


async def test_a_post_with_the_wrong_csrf_token_is_refused(harness: Harness) -> None:
    await csrf_token(harness.client)
    response = await harness.client.post(
        "/login",
        data={"email": EMAIL, "password": STRONG, "csrf_token": "forged-token"},
        follow_redirects=False,
    )
    assert response.status_code == 403


async def test_a_get_mints_a_csrf_cookie(harness: Harness) -> None:
    response = await harness.client.get("/signup")
    assert response.cookies.get("__Host-csrf")


async def test_an_idle_session_expires_after_thirty_minutes(harness: Harness) -> None:
    await sign_up(harness)
    await log_in(harness)

    harness.clock.advance(minutes=31)
    page = await harness.client.get("/app", follow_redirects=False)

    assert page.status_code == 303
    assert page.headers["location"] == "/login"


async def test_repeated_failures_are_throttled(harness: Harness) -> None:
    await sign_up(harness)
    await log_out(harness)

    for _ in range(6):
        await form_post(
            harness.client, "/login", {"email": EMAIL, "password": "wrong-passphrase-2026"}
        )

    blocked = await form_post(harness.client, "/login", {"email": EMAIL, "password": STRONG})
    assert blocked.status_code == 429
    assert "Too many attempts" in blocked.text


async def test_a_successful_login_clears_the_failure_counter(harness: Harness) -> None:
    await sign_up(harness)
    for _ in range(3):
        await form_post(
            harness.client, "/login", {"email": EMAIL, "password": "wrong-passphrase-2026"}
        )
    harness.clock.advance(minutes=10)

    good = await log_in(harness)
    assert good.status_code == 303
    state = await harness.store.throttle(f"email:{EMAIL}")
    assert state.failures == 0
