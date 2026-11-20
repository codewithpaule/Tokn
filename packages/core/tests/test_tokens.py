from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tokn_core.auth.hashing import digests_match, hash_secret, new_secret
from tokn_core.auth.tokens import (
    RECOVERY_CODE_COUNT,
    TOKEN_TTL,
    TokenKind,
    digest_for,
    is_expired,
    issue_token,
    new_recovery_codes,
)

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
PEPPER = "test-pepper-not-a-real-secret"


def test_a_token_is_shown_raw_and_stored_hashed() -> None:
    token = issue_token(TokenKind.PASSWORD_RESET, NOW, PEPPER)
    assert token.raw != token.digest
    assert digest_for(token.raw, PEPPER) == token.digest
    assert token.kind is TokenKind.PASSWORD_RESET


def test_a_token_expires_after_thirty_minutes() -> None:
    token = issue_token(TokenKind.EMAIL_VERIFICATION, NOW, PEPPER)
    assert timedelta(minutes=30) == TOKEN_TTL
    assert not is_expired(token, NOW + TOKEN_TTL - timedelta(seconds=1))
    assert is_expired(token, NOW + TOKEN_TTL)


def test_tokens_are_unique() -> None:
    raws = {issue_token(TokenKind.PASSWORD_RESET, NOW, PEPPER).raw for _ in range(200)}
    assert len(raws) == 200


def test_the_pepper_changes_the_digest() -> None:
    token = issue_token(TokenKind.PASSWORD_RESET, NOW, PEPPER)
    assert digest_for(token.raw, "another-pepper") != token.digest


def test_hashing_without_a_pepper_is_a_configuration_error() -> None:
    with pytest.raises(ValueError, match="pepper"):
        hash_secret("value", "")


def test_comparison_is_constant_time_and_correct() -> None:
    assert digests_match("abc", "abc")
    assert not digests_match("abc", "abd")


def test_recovery_codes_are_ten_hashed_single_use_strings() -> None:
    codes = new_recovery_codes(PEPPER)
    assert len(codes) == RECOVERY_CODE_COUNT == 10
    raws = [raw for raw, _ in codes]
    digests = [digest for _, digest in codes]

    assert len(set(raws)) == 10
    assert len(set(digests)) == 10
    for raw, digest in codes:
        assert len(raw) == 9
        assert raw[4] == "-"
        assert digest == hash_secret(raw, PEPPER)


def test_new_secrets_carry_enough_entropy() -> None:
    secrets = {new_secret() for _ in range(100)}
    assert len(secrets) == 100
    assert all(len(secret) >= 43 for secret in secrets)
