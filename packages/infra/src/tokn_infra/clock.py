"""A clock backed by the operating system. The only place that reads the wall clock."""

from __future__ import annotations

from datetime import UTC, datetime


class SystemClock:
    """Implements ``tokn_core.auth.ports.Clock``."""

    def now(self) -> datetime:
        return datetime.now(tz=UTC)
