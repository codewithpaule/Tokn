"""RFC 6238 test vectors, truncated to the six digits we issue."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tokn_core.auth.totp import (
    STEP_SECONDS,
    code_at,
    generate_secret,
    provisioning_uri,
    verify,
)

# base32 of the RFC's ASCII secret "12345678901234567890". Published test vector.
RFC_SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # noqa: S105

RFC_VECTORS = [
    (59, "287082"),
    (1_111_111_109, "081804"),
    (1_111_111_111, "050471"),
    (1_234_567_890, "005924"),
    (2_000_000_000, "279037"),
    (20_000_000_000, "353130"),
]


@pytest.mark.parametrize(("unix_time", "expected"), RFC_VECTORS)
def test_matches_the_rfc_vectors(unix_time: int, expected: str) -> None:
    assert code_at(RFC_SECRET, datetime.fromtimestamp(unix_time, tz=UTC)) == expected


def test_generated_secrets_are_distinct_and_decodable() -> None:
    secrets = {generate_secret() for _ in range(50)}
    assert len(secrets) == 50
    for secret in secrets:
        assert code_at(secret, datetime.now(tz=UTC)).isdigit()


def test_a_code_is_valid_for_one_step_either_side() -> None:
    at = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
    code = code_at(RFC_SECRET, at)

    assert verify(RFC_SECRET, code, at)
    assert verify(RFC_SECRET, code, at + timedelta(seconds=STEP_SECONDS))
    assert verify(RFC_SECRET, code, at - timedelta(seconds=STEP_SECONDS))
    assert not verify(RFC_SECRET, code, at + timedelta(seconds=STEP_SECONDS * 2))
    assert not verify(RFC_SECRET, code, at - timedelta(seconds=STEP_SECONDS * 2))


def test_a_different_secret_does_not_verify() -> None:
    at = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
    code = code_at(generate_secret(), at)
    assert not verify(RFC_SECRET, code, at)


@pytest.mark.parametrize("bad", ["", "12345", "1234567", "abcdef", "12 34 5"])
def test_malformed_codes_are_refused_without_raising(bad: str) -> None:
    at = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
    assert verify(RFC_SECRET, bad, at) is False


def test_invalid_base32_secret_raises_a_clear_error() -> None:
    with pytest.raises(ValueError, match="base32"):
        code_at("not-a-secret!!", datetime.now(tz=UTC))


def test_provisioning_uri_is_scannable() -> None:
    uri = provisioning_uri(RFC_SECRET, "engineer@tokn.ng", "Tokn")
    assert uri.startswith("otpauth://totp/")
    assert "secret=" + RFC_SECRET in uri
    assert "issuer=Tokn" in uri
    assert "period=30" in uri
    assert "engineer%40tokn.ng" in uri
