"""Key issuance: two-factor first, the secret shown once, revocation immediate."""

from __future__ import annotations

import re

import pytest
from support import EMAIL, Harness, enable_two_factor, form_post, log_in, sign_up

from tokn_console.config import Settings
from tokn_core.api_keys import KeyRecord
from tokn_core.auth.totp import code_at
from tokn_core.errors import AuthenticationFailedError
from tokn_core.key_auth import authenticate
from tokn_core.money import Naira

RAW_KEY = re.compile(r"tokn_live_[0-9a-f]{64}")
PEPPER = Settings(env="development").key_pepper


async def account_id(harness: Harness) -> str:
    account = await harness.store.account_by_email(EMAIL)
    assert account is not None
    return account.id


async def create_key(harness: Harness, secret: str, **fields: str):
    return await form_post(
        harness.client,
        "/app/keys",
        {"name": "laptop", "code": code_at(secret, harness.clock.now()), **fields},
    )


async def test_a_key_cannot_be_created_before_two_factor(harness: Harness) -> None:
    await sign_up(harness)
    await log_in(harness)

    response = await form_post(harness.client, "/app/keys", {"name": "laptop", "code": "123456"})

    assert response.status_code == 303
    assert response.headers["location"] == "/app/2fa"
    assert await harness.key_store.list_for_account(await account_id(harness)) == ()


async def test_a_key_needs_a_current_code(harness: Harness) -> None:
    await sign_up(harness)
    await enable_two_factor(harness)

    response = await form_post(harness.client, "/app/keys", {"name": "laptop", "code": "000000"})

    assert response.status_code == 422
    assert "did not match" in response.text
    assert await harness.key_store.list_for_account(await account_id(harness)) == ()


async def test_the_secret_is_shown_once_and_only_a_digest_is_stored(harness: Harness) -> None:
    await sign_up(harness)
    secret, _ = await enable_two_factor(harness)

    created = await create_key(harness, secret)
    assert created.status_code == 200
    match = RAW_KEY.search(created.text)
    assert match is not None, "the raw key must be shown exactly once"
    raw = match.group(0)

    stored = await harness.key_store.list_for_account(await account_id(harness))
    assert len(stored) == 1
    assert stored[0].digest != raw
    assert raw not in stored[0].digest
    assert raw.startswith(stored[0].prefix)

    listing = await harness.client.get("/app/keys")
    assert raw not in listing.text, "the secret must never be shown again"
    assert stored[0].prefix in listing.text


async def test_a_created_key_authenticates_at_the_gateway(harness: Harness) -> None:
    await sign_up(harness)
    secret, _ = await enable_two_factor(harness)
    created = await create_key(harness, secret)
    raw = RAW_KEY.search(created.text)
    assert raw is not None

    record = await authenticate(
        presented=raw.group(0),
        store=harness.key_store,
        cache=harness.key_cache,
        pepper=PEPPER,
        clock=harness.clock,
    )
    assert record.account_id == await account_id(harness)
    assert record.has_scope()


async def test_a_spend_cap_is_parsed_from_naira(harness: Harness) -> None:
    await sign_up(harness)
    secret, _ = await enable_two_factor(harness)

    await create_key(harness, secret, spend_cap_naira="2,500")

    stored = await harness.key_store.list_for_account(await account_id(harness))
    assert stored[0].spend_cap_micro == Naira.from_naira("2500").micro


async def test_an_unparseable_cap_is_refused(harness: Harness) -> None:
    await sign_up(harness)
    secret, _ = await enable_two_factor(harness)

    response = await create_key(harness, secret, spend_cap_naira="lots")

    assert response.status_code == 422
    assert "in naira" in response.text


async def test_revocation_makes_the_key_unusable_immediately(harness: Harness) -> None:
    await sign_up(harness)
    secret, _ = await enable_two_factor(harness)
    created = await create_key(harness, secret)
    raw = RAW_KEY.search(created.text)
    assert raw is not None

    stored = await harness.key_store.list_for_account(await account_id(harness))
    revoked = await form_post(
        harness.client,
        "/app/keys/revoke",
        {"key_id": stored[0].id, "code": code_at(secret, harness.clock.now())},
    )
    assert revoked.status_code == 303

    with pytest.raises(AuthenticationFailedError):
        await authenticate(
            presented=raw.group(0),
            store=harness.key_store,
            cache=harness.key_cache,
            pepper=PEPPER,
            clock=harness.clock,
        )


async def test_another_accounts_key_cannot_be_revoked(harness: Harness) -> None:
    await sign_up(harness)
    secret, _ = await enable_two_factor(harness)

    foreign = KeyRecord(
        id="someone-elses-key",
        account_id="another-account",
        prefix="tokn_live_abcdef",
        digest="not-used-here",
        scopes=frozenset({"infer"}),
    )
    await harness.key_store.create(foreign)

    response = await form_post(
        harness.client,
        "/app/keys/revoke",
        {"key_id": foreign.id, "code": code_at(secret, harness.clock.now())},
    )
    assert response.status_code == 404
    still_there = await harness.key_store.by_id(foreign.id)
    assert still_there is not None
    assert still_there.revoked_at is None
