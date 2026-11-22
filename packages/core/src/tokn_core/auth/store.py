"""The persistence port for authentication.

The console depends on this protocol, never on SQLAlchemy. Records are plain dataclasses
with ``str`` ids so the domain stays free of database types.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from tokn_core.auth.throttle import ThrottleState
from tokn_core.auth.tokens import TokenKind

ACCOUNT_PENDING = "pending"
ACCOUNT_ACTIVE = "active"
ACCOUNT_SUSPENDED = "suspended"


@dataclass(frozen=True, slots=True)
class AccountRecord:
    id: str
    email: str
    password_hash: str | None
    email_verified: bool
    totp_secret: str | None = None
    status: str = ACCOUNT_PENDING
    recovery_code_digests: tuple[str, ...] = field(default=())
    is_admin: bool = False

    @property
    def can_log_in(self) -> bool:
        return self.status == ACCOUNT_ACTIVE


@dataclass(frozen=True, slots=True)
class SessionRecord:
    digest: str
    account_id: str
    created_at: datetime
    last_seen_at: datetime


class ThrottleStore(Protocol):
    """Ephemeral counters. Losing them costs a burst of attempts, never money."""

    async def throttle(self, identity: str) -> ThrottleState: ...

    async def save_throttle(self, identity: str, state: ThrottleState) -> None: ...


class AuthStore(Protocol):
    """Everything the auth use cases need from durable storage."""

    async def create_account(self, email: str, password_hash: str) -> AccountRecord:
        """Insert a pending account. Raises if the email is already registered."""

    async def account_by_email(self, email: str) -> AccountRecord | None: ...

    async def account_by_id(self, account_id: str) -> AccountRecord | None: ...

    async def set_email_verified(self, account_id: str, now: datetime) -> None: ...

    async def set_password_hash(self, account_id: str, password_hash: str) -> None: ...

    async def set_totp_secret(self, account_id: str, secret: str) -> None: ...

    async def set_recovery_codes(self, account_id: str, digests: tuple[str, ...]) -> None: ...

    async def set_admin(self, account_id: str, value: bool = True) -> None: ...

    async def consume_recovery_code(self, account_id: str, digest: str) -> bool:
        """Remove one matching recovery code. True if it was present."""

    async def issue_token(
        self, account_id: str, digest: str, kind: TokenKind, expires_at: datetime
    ) -> None: ...

    async def consume_token(self, digest: str, kind: TokenKind, now: datetime) -> str | None:
        """Return the account id and burn the token, or None if absent, used or expired."""

    async def create_session(
        self, digest: str, account_id: str, now: datetime, ip: str | None, user_agent: str | None
    ) -> None: ...

    async def session(self, digest: str) -> SessionRecord | None: ...

    async def touch_session(self, digest: str, now: datetime) -> None: ...

    async def delete_session(self, digest: str) -> None: ...

    async def delete_sessions(self, account_id: str, *, keep: str | None = None) -> None:
        """Drop every session for an account, optionally sparing one by digest."""

    async def throttle(self, identity: str) -> ThrottleState: ...

    async def save_throttle(self, identity: str, state: ThrottleState) -> None: ...

    async def enqueue_email(self, to: str, subject: str, body: str) -> None:
        """Write to the outbox. Never sends inline."""
