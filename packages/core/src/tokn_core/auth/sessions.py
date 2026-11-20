"""Server-side sessions.

The browser holds a 256-bit random id in a ``__Host-`` cookie. PostgreSQL holds only its
HMAC hash, so a stolen database cannot be replayed as a session.

A session ends on the earlier of two clocks: 30 minutes of inactivity, or 7 days after it
was created. The id is rotated on login and on any privilege change.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from tokn_core.auth.hashing import new_secret
from tokn_core.errors import DomainError

COOKIE_NAME = "__Host-session"
IDLE_TIMEOUT = timedelta(minutes=30)
ABSOLUTE_TIMEOUT = timedelta(days=7)


class SessionExpiredError(DomainError):
    """The session is missing, idle for too long, or past its absolute lifetime."""


@dataclass(frozen=True, slots=True)
class Session:
    account_id: str
    created_at: datetime
    last_seen_at: datetime
    ip: str | None = None
    user_agent: str | None = None

    def is_expired(self, now: datetime) -> bool:
        if now - self.created_at >= ABSOLUTE_TIMEOUT:
            return True
        return now - self.last_seen_at >= IDLE_TIMEOUT

    def touch(self, now: datetime) -> Session:
        """Record activity, refusing to revive an expired session."""
        if self.is_expired(now):
            raise SessionExpiredError("session has expired")
        return replace(self, last_seen_at=now)

    def expires_at(self, now: datetime) -> datetime:
        """When this session will end, given it stays idle from ``now``."""
        return min(self.last_seen_at + IDLE_TIMEOUT, self.created_at + ABSOLUTE_TIMEOUT)


def new_session(
    account_id: str, now: datetime, ip: str | None, user_agent: str | None
) -> tuple[str, Session]:
    """Create a session and return the raw id to put in the cookie, plus the record."""
    return new_secret(), Session(
        account_id=account_id,
        created_at=now,
        last_seen_at=now,
        ip=ip,
        user_agent=user_agent,
    )
