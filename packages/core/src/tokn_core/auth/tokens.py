"""Single-use tokens and recovery codes.

Email verification, password reset and recovery codes share one rule: the raw value is
shown to the user exactly once, the server stores only a keyed digest, and the record can
be consumed exactly once within 30 minutes.
"""

from __future__ import annotations

import base64
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from tokn_core.auth.hashing import hash_secret, new_secret

TOKEN_TTL = timedelta(minutes=30)
RECOVERY_CODE_COUNT = 10
RECOVERY_CODE_BYTES = 5


class TokenKind(StrEnum):
    EMAIL_VERIFICATION = "email_verification"
    PASSWORD_RESET = "password_reset"  # noqa: S105 - a token kind, not a credential


@dataclass(frozen=True, slots=True)
class IssuedToken:
    """A token in both forms: ``raw`` goes to the user, ``digest`` goes to the database."""

    raw: str
    digest: str
    kind: TokenKind
    expires_at: datetime


def digest_for(raw: str, pepper: str) -> str:
    return hash_secret(raw, pepper)


def issue_token(kind: TokenKind, now: datetime, pepper: str) -> IssuedToken:
    raw = new_secret()
    return IssuedToken(
        raw=raw,
        digest=digest_for(raw, pepper),
        kind=kind,
        expires_at=now + TOKEN_TTL,
    )


def is_expired(token: IssuedToken, now: datetime) -> bool:
    return now >= token.expires_at


def _recovery_code() -> str:
    raw = base64.b32encode(secrets.token_bytes(RECOVERY_CODE_BYTES)).decode("ascii")
    return f"{raw[:4]}-{raw[4:8]}"


def new_recovery_codes(pepper: str) -> list[tuple[str, str]]:
    """Ten single-use codes as ``(raw, digest)`` pairs. Shown once, stored hashed."""
    codes: list[tuple[str, str]] = []
    for _ in range(RECOVERY_CODE_COUNT):
        code = _recovery_code()
        codes.append((code, hash_secret(code, pepper)))
    return codes
