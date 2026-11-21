"""In-memory implementation of the auth store.

Used by the console's security tests and by local development, where starting PostgreSQL
is not worth the ceremony. It is not durable and says so: nothing here survives a restart.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from tokn_core.auth.store import AccountRecord, AuthStore, SessionRecord
from tokn_core.auth.throttle import ThrottleState
from tokn_core.auth.tokens import TokenKind
from tokn_core.errors import AccountExistsError


@dataclass(slots=True)
class _Token:
    account_id: str
    kind: TokenKind
    expires_at: datetime
    used: bool = False


class InMemoryAuthStore:
    """A complete, thread-unsafe ``AuthStore`` held in dictionaries."""

    def __init__(self) -> None:
        self._accounts: dict[str, AccountRecord] = {}
        self._ids_by_email: dict[str, str] = {}
        self._tokens: dict[str, _Token] = {}
        self._sessions: dict[str, SessionRecord] = {}
        self._throttle: dict[str, ThrottleState] = {}
        self.emails: list[tuple[str, str, str]] = []
        self._next_id = 0

    async def create_account(self, email: str, password_hash: str) -> AccountRecord:
        normalised = email.strip().lower()
        if normalised in self._ids_by_email:
            raise AccountExistsError(normalised)
        self._next_id += 1
        account = AccountRecord(
            id=f"acc_{self._next_id}",
            email=normalised,
            password_hash=password_hash,
            email_verified=False,
        )
        self._accounts[account.id] = account
        self._ids_by_email[normalised] = account.id
        return account

    async def account_by_email(self, email: str) -> AccountRecord | None:
        account_id = self._ids_by_email.get(email.strip().lower())
        return None if account_id is None else self._accounts[account_id]

    async def account_by_id(self, account_id: str) -> AccountRecord | None:
        return self._accounts.get(account_id)

    async def _update(self, account_id: str, **changes: object) -> None:
        self._accounts[account_id] = replace(self._accounts[account_id], **changes)  # type: ignore[arg-type]

    async def set_email_verified(self, account_id: str, now: datetime) -> None:
        await self._update(account_id, email_verified=True, status="active")

    async def set_password_hash(self, account_id: str, password_hash: str) -> None:
        await self._update(account_id, password_hash=password_hash)

    async def set_totp_secret(self, account_id: str, secret: str) -> None:
        await self._update(account_id, totp_secret=secret)

    async def set_recovery_codes(self, account_id: str, digests: tuple[str, ...]) -> None:
        await self._update(account_id, recovery_code_digests=digests)

    async def consume_recovery_code(self, account_id: str, digest: str) -> bool:
        account = self._accounts[account_id]
        if digest not in account.recovery_code_digests:
            return False
        remaining = tuple(d for d in account.recovery_code_digests if d != digest)
        await self._update(account_id, recovery_code_digests=remaining)
        return True

    async def issue_token(
        self, account_id: str, digest: str, kind: TokenKind, expires_at: datetime
    ) -> None:
        self._tokens[digest] = _Token(account_id=account_id, kind=kind, expires_at=expires_at)

    async def consume_token(self, digest: str, kind: TokenKind, now: datetime) -> str | None:
        token = self._tokens.get(digest)
        if token is None or token.used or token.kind is not kind or now >= token.expires_at:
            return None
        token.used = True
        return token.account_id

    async def create_session(
        self,
        digest: str,
        account_id: str,
        now: datetime,
        ip: str | None,
        user_agent: str | None,
    ) -> None:
        self._sessions[digest] = SessionRecord(
            digest=digest, account_id=account_id, created_at=now, last_seen_at=now
        )

    async def session(self, digest: str) -> SessionRecord | None:
        return self._sessions.get(digest)

    async def touch_session(self, digest: str, now: datetime) -> None:
        record = self._sessions.get(digest)
        if record is not None:
            self._sessions[digest] = replace(record, last_seen_at=now)

    async def delete_session(self, digest: str) -> None:
        self._sessions.pop(digest, None)

    async def delete_sessions(self, account_id: str, *, keep: str | None = None) -> None:
        for digest, record in list(self._sessions.items()):
            if record.account_id == account_id and digest != keep:
                del self._sessions[digest]

    async def throttle(self, identity: str) -> ThrottleState:
        return self._throttle.get(identity, ThrottleState())

    async def save_throttle(self, identity: str, state: ThrottleState) -> None:
        self._throttle[identity] = state

    async def enqueue_email(self, to: str, subject: str, body: str) -> None:
        self.emails.append((to, subject, body))

    async def make_admin(self, account_id: str, value: bool = True) -> None:
        """Local development and tests only: promote an account."""
        await self._update(account_id, is_admin=value)

    def token_for(self, kind: TokenKind) -> str | None:
        """Test helper: the most recent raw digest of a kind, for asserting single use."""
        for digest, token in self._tokens.items():
            if token.kind is kind and not token.used:
                return digest
        return None


__all__ = ["AuthStore", "InMemoryAuthStore"]
