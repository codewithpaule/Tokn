"""Ports the auth use cases depend on.

Every adapter that touches a clock, a password hash or a session store implements one of
these, so the use cases can be tested with fakes and never import a framework.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime:
        """The current instant, timezone-aware, from a single source of truth."""


class PasswordHasher(Protocol):
    def hash(self, password: str) -> str:
        """Argon2id, with the parameters the deployment has decided on."""

    def verify(self, password: str, hashed: str) -> bool:
        """Constant-time check. False, never an exception, for a malformed hash."""

    def needs_rehash(self, hashed: str) -> bool:
        """True when ``hashed`` predates the current cost parameters."""
