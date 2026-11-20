from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from tokn_core.api_keys import (
    ENVIRONMENTS,
    KEY_BYTES,
    KeyFormatError,
    KeyRecord,
    generate_key,
    matches,
    parse_prefix,
    redact,
)
from tokn_core.auth.hashing import hash_secret

PEPPER = "test-pepper-not-a-real-secret"
NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)


def record(**changes: object) -> KeyRecord:
    key = generate_key(pepper=PEPPER)
    base = KeyRecord(
        id="key_1",
        account_id="acc_1",
        prefix=key.prefix,
        digest=key.digest,
        scopes=frozenset({"infer"}),
    )
    return replace(base, **changes)  # type: ignore[arg-type]


def test_a_generated_key_has_the_published_shape() -> None:
    key = generate_key(pepper=PEPPER)
    head, environment, body = key.raw.split("_")

    assert head == "tokn"
    assert environment == "live"
    assert len(body) == KEY_BYTES * 2
    assert key.prefix == f"tokn_live_{body[:6]}"
    assert key.digest == hash_secret(key.raw, PEPPER)
    assert key.raw not in key.digest


def test_the_environment_marker_is_carried_through() -> None:
    key = generate_key(pepper=PEPPER, environment="test")
    assert key.raw.startswith("tokn_test_")


def test_an_unknown_environment_or_odd_product_is_refused() -> None:
    with pytest.raises(KeyFormatError):
        generate_key(pepper=PEPPER, environment="staging")
    with pytest.raises(KeyFormatError):
        generate_key(pepper=PEPPER, product="tokn_live")


def test_prefixes_are_unique_across_many_keys() -> None:
    prefixes = {generate_key(pepper=PEPPER).prefix for _ in range(500)}
    assert len(prefixes) == 500


def test_only_the_right_secret_matches() -> None:
    key = generate_key(pepper=PEPPER)
    stored = record(prefix=key.prefix, digest=key.digest)
    assert matches(key.raw, stored, PEPPER)
    assert not matches(key.raw + "0", stored, PEPPER)
    assert not matches(key.raw[:-1], stored, PEPPER)
    assert not matches(key.raw, stored, "a-different-pepper")


@pytest.mark.parametrize(
    "candidate",
    [
        "",
        "tokn_live_short",
        "other_live_" + "a" * 64,
        "tokn_prod_" + "a" * 64,
        "tokn_live_" + "z" * 64,
        "tokn_live_" + "a" * 63,
        "tokn_live_" + "a" * 65,
        "tokn_live_" + "A" * 64,
    ],
)
def test_malformed_keys_have_no_prefix(candidate: str) -> None:
    assert parse_prefix(candidate) is None


def test_a_good_key_parses_to_its_visible_prefix() -> None:
    key = generate_key(pepper=PEPPER)
    assert parse_prefix(key.raw) == key.prefix
    assert set(ENVIRONMENTS) == {"live", "test"}


def test_revoked_expired_paused_and_capped_keys_are_unusable() -> None:
    assert record().is_usable(NOW)
    assert not record(revoked_at=NOW).is_usable(NOW)
    assert not record(auto_paused=True).is_usable(NOW)
    assert not record(expires_at=NOW).is_usable(NOW)
    assert not record(expires_at=NOW + timedelta(seconds=1)).is_usable(NOW + timedelta(seconds=1))
    assert not record(spend_cap_micro=100, spent_micro=100).is_usable(NOW)
    assert record(spend_cap_micro=100, spent_micro=99).is_usable(NOW)


def test_an_ip_allowlist_is_exclusive_when_present() -> None:
    assert record().permits(None)
    assert record().permits("203.0.113.9")

    limited = record(ip_allowlist=("203.0.113.9",))
    assert limited.permits("203.0.113.9")
    assert not limited.permits("198.51.100.1")
    assert not limited.permits(None)


def test_scopes_are_required_by_name() -> None:
    assert record().has_scope("infer")
    assert not record().has_scope("admin")
    assert not record(scopes=frozenset()).has_scope()


def test_redaction_keeps_only_the_prefix() -> None:
    key = generate_key(pepper=PEPPER)
    assert redact(key.prefix) == f"{key.prefix}…"
    assert key.raw not in redact(key.prefix)
