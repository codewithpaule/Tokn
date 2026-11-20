"""Mandatory two-factor: enrolment, TOTP login, and single-use recovery codes."""

from __future__ import annotations

from support import (
    EMAIL,
    STRONG,
    Harness,
    enable_two_factor,
    form_post,
    log_in,
    log_out,
    sign_up,
)

from tokn_core.auth.totp import code_at


async def test_enrolling_returns_ten_recovery_codes(harness: Harness) -> None:
    await sign_up(harness)
    secret, codes = await enable_two_factor(harness)

    assert len(secret) >= 16
    assert len(codes) == 10
    assert len(set(codes)) == 10

    account = await harness.store.account_by_email(EMAIL)
    assert account is not None
    assert account.totp_secret == secret
    assert len(account.recovery_code_digests) == 10
    assert all(code not in account.recovery_code_digests for code in codes), (
        "raw recovery codes must never be stored"
    )


async def test_a_wrong_code_does_not_enable_two_factor(harness: Harness) -> None:
    await sign_up(harness)
    await log_in(harness)
    page = await harness.client.get("/app/2fa")
    secret = page.text.split('name="secret" value="')[1].split('"')[0]

    response = await form_post(harness.client, "/app/2fa", {"secret": secret, "code": "000000"})
    assert response.status_code == 422
    assert "did not match" in response.text

    account = await harness.store.account_by_email(EMAIL)
    assert account is not None
    assert account.totp_secret is None


async def test_login_without_the_code_is_refused_once_enrolled(harness: Harness) -> None:
    await sign_up(harness)
    await enable_two_factor(harness)
    await log_out(harness)

    response = await log_in(harness)
    assert response.status_code == 401
    assert "code is incorrect" in response.text


async def test_login_with_a_valid_totp_code_succeeds(harness: Harness) -> None:
    await sign_up(harness)
    secret, _ = await enable_two_factor(harness)
    await log_out(harness)

    harness.clock.advance(seconds=30)
    response = await log_in(harness, code=code_at(secret, harness.clock.now()))

    assert response.status_code == 303
    page = await harness.client.get("/app")
    assert EMAIL in page.text


async def test_a_recovery_code_works_exactly_once(harness: Harness) -> None:
    await sign_up(harness)
    _, codes = await enable_two_factor(harness)
    await log_out(harness)

    first = await log_in(harness, code=codes[0])
    assert first.status_code == 303
    await log_out(harness)

    second = await log_in(harness, code=codes[0])
    assert second.status_code == 401


async def test_the_authenticated_area_requires_a_session(harness: Harness) -> None:
    await sign_up(harness)
    await enable_two_factor(harness)
    await log_out(harness)

    page = await harness.client.get("/app/2fa", follow_redirects=False)
    assert page.status_code == 303
    assert page.headers["location"] == "/login"


async def test_the_enrolment_uri_is_scannable(harness: Harness) -> None:
    await sign_up(harness)
    await log_in(harness)
    page = await harness.client.get("/app/2fa")

    assert "otpauth://totp/" in page.text
    assert "issuer=Tokn" in page.text
    assert STRONG not in page.text
