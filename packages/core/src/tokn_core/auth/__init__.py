"""Authentication rules: passwords, TOTP, sessions, single-use tokens, throttling.

Framework-free. Argon2 lives behind the ``PasswordHasher`` port; everything else here is
standard library so it can be reasoned about and tested without infrastructure.
"""

from tokn_core.auth.hashing import digests_match, hash_secret, new_secret
from tokn_core.auth.passwords import (
    MAX_LENGTH,
    MIN_LENGTH,
    WeakPasswordError,
    validate_password,
)
from tokn_core.auth.ports import Clock, PasswordHasher
from tokn_core.auth.sessions import (
    ABSOLUTE_TIMEOUT,
    COOKIE_NAME,
    IDLE_TIMEOUT,
    Session,
    SessionExpiredError,
    new_session,
)
from tokn_core.auth.throttle import ThrottleState, backoff_delay
from tokn_core.auth.tokens import (
    RECOVERY_CODE_COUNT,
    TOKEN_TTL,
    IssuedToken,
    TokenKind,
    digest_for,
    is_expired,
    issue_token,
    new_recovery_codes,
)
from tokn_core.auth.totp import code_at, generate_secret, provisioning_uri, verify

__all__ = [
    "ABSOLUTE_TIMEOUT",
    "COOKIE_NAME",
    "IDLE_TIMEOUT",
    "MAX_LENGTH",
    "MIN_LENGTH",
    "RECOVERY_CODE_COUNT",
    "TOKEN_TTL",
    "Clock",
    "IssuedToken",
    "PasswordHasher",
    "Session",
    "SessionExpiredError",
    "ThrottleState",
    "TokenKind",
    "WeakPasswordError",
    "backoff_delay",
    "code_at",
    "digest_for",
    "digests_match",
    "generate_secret",
    "hash_secret",
    "is_expired",
    "issue_token",
    "new_recovery_codes",
    "new_secret",
    "new_session",
    "provisioning_uri",
    "validate_password",
    "verify",
]
