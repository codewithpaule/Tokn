"""The happy paths, and the places where they deliberately look identical to failures."""

from __future__ import annotations

from httpx import AsyncClient
from support import (
    EMAIL,
    STRONG,
    Harness,
    form_post,
    last_link,
    log_in,
    sign_up,
)


async def test_signup_creates_a_pending_account_and_queues_an_email(harness: Harness) -> None:
    response = await form_post(harness.client, "/signup", {"email": EMAIL, "password": STRONG})
    assert response.status_code == 200
    assert "Check your email" in response.text

    account = await harness.store.account_by_email(EMAIL)
    assert account is not None
    assert account.email_verified is False
    assert account.password_hash is not None
    assert account.password_hash != STRONG, "the password must never be stored in clear"
    assert len(harness.store.emails) == 1
    assert "/verify?token=" in harness.store.emails[0][2]


async def test_verification_link_activates_the_account_once(harness: Harness) -> None:
    await sign_up(harness, verify=False)
    link = last_link(harness.store)

    first = await harness.client.get(link)
    second = await harness.client.get(link)

    assert first.status_code == 200
    assert "Email verified" in first.text
    assert second.status_code == 400
    assert "not valid" in second.text

    account = await harness.store.account_by_email(EMAIL)
    assert account is not None
    assert account.email_verified


async def test_login_before_verification_is_refused(harness: Harness) -> None:
    await sign_up(harness, verify=False)
    response = await log_in(harness)
    assert response.status_code == 403
    assert "not active" in response.text


async def test_login_sets_a_session_and_reaches_the_app(harness: Harness) -> None:
    await sign_up(harness)
    response = await log_in(harness)

    assert response.status_code == 303
    assert response.headers["location"] == "/app"
    cookie = harness.client.cookies.get("__Host-session")
    assert cookie

    page = await harness.client.get("/app")
    assert page.status_code == 200
    assert EMAIL in page.text


async def test_logout_destroys_the_server_side_session(harness: Harness) -> None:
    await sign_up(harness)
    await log_in(harness)
    assert harness.client.cookies.get("__Host-session")

    response = await form_post(harness.client, "/logout")
    assert response.status_code == 303
    await _assert_anonymous(harness.client)


async def test_an_unknown_email_and_a_wrong_password_look_the_same(harness: Harness) -> None:
    await sign_up(harness)

    unknown = await form_post(
        harness.client, "/login", {"email": "nobody@tokn.ng", "password": STRONG}
    )
    harness.clock.advance(seconds=5)
    wrong = await form_post(
        harness.client, "/login", {"email": EMAIL, "password": "wrong-passphrase-2026"}
    )

    assert unknown.status_code == wrong.status_code == 401
    assert unknown.text == wrong.text
    assert "Email or password is incorrect" in unknown.text


async def test_signup_does_not_reveal_a_registered_address(harness: Harness) -> None:
    await sign_up(harness)

    again = await form_post(harness.client, "/signup", {"email": EMAIL, "password": STRONG})
    assert again.status_code == 200
    assert "Check your email" in again.text
    assert len(harness.store.emails) == 1, "no second email is sent for an existing account"


async def test_a_weak_password_is_refused_with_a_reason(harness: Harness) -> None:
    response = await form_post(
        harness.client, "/signup", {"email": EMAIL, "password": "password1234"}
    )
    assert response.status_code == 422
    assert "breach" in response.text
    assert await harness.store.account_by_email(EMAIL) is None


async def _assert_anonymous(client: AsyncClient) -> None:
    page = await client.get("/app", follow_redirects=False)
    assert page.status_code == 303
    assert page.headers["location"] == "/login"
