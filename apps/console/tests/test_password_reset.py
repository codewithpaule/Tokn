"""Password reset: no enumeration, single use, and sessions die with the old password."""

from __future__ import annotations

from support import (
    EMAIL,
    ROTATED,
    STRONG,
    Harness,
    form_post,
    last_link,
    log_in,
    log_out,
    sign_up,
)


async def _request_reset(harness: Harness, email: str):
    return await form_post(harness.client, "/reset", {"email": email})


async def test_reset_request_is_identical_for_known_and_unknown_addresses(
    harness: Harness,
) -> None:
    await sign_up(harness)
    await log_out(harness)

    known = await _request_reset(harness, EMAIL)
    unknown = await _request_reset(harness, "nobody@tokn.ng")

    assert known.status_code == unknown.status_code == 200
    assert known.text == unknown.text
    assert len(harness.store.emails) == 2, "only the real account gets a link"


async def test_the_reset_link_works_once(harness: Harness) -> None:
    await sign_up(harness)
    await log_out(harness)
    await _request_reset(harness, EMAIL)
    link = last_link(harness.store)
    token = link.split("token=", 1)[1]

    first = await form_post(
        harness.client, "/reset/confirm", {"token": token, "password": "a-brand-new-passphrase"}
    )
    second = await form_post(
        harness.client, "/reset/confirm", {"token": token, "password": "another-passphrase-x"}
    )

    assert first.status_code == 200
    assert "Password changed" in first.text
    assert second.status_code == 400


async def test_resetting_the_password_ends_every_session(harness: Harness) -> None:
    await sign_up(harness)
    await log_in(harness)

    await log_out(harness)
    await _request_reset(harness, EMAIL)
    token = last_link(harness.store).split("token=", 1)[1]
    await form_post(
        harness.client, "/reset/confirm", {"token": token, "password": "a-brand-new-passphrase"}
    )

    old_password = await log_in(harness, password=STRONG)
    assert old_password.status_code != 303
    harness.clock.advance(seconds=30)
    changed = await log_in(harness, password=ROTATED)
    assert changed.status_code == 303


async def test_a_weak_new_password_is_refused_and_the_link_still_works(harness: Harness) -> None:
    await sign_up(harness)
    await log_out(harness)
    await _request_reset(harness, EMAIL)
    token = last_link(harness.store).split("token=", 1)[1]

    weak = await form_post(
        harness.client, "/reset/confirm", {"token": token, "password": "password1234"}
    )
    assert weak.status_code == 422

    good = await form_post(
        harness.client, "/reset/confirm", {"token": token, "password": "a-brand-new-passphrase"}
    )
    assert good.status_code == 200
