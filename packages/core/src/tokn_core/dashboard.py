"""Read models for the customer dashboard.

The console shows money and usage; it never writes either. These three records are the
shape every adapter returns, so the screens do not know whether they are reading
PostgreSQL or the in-process development store.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True, slots=True)
class LedgerLine:
    """One movement of money. ``amount_micro`` is signed."""

    kind: str
    amount_micro: int
    balance_after_micro: int
    reference: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class UsageLine:
    """One billed request. Metadata only: never the prompt or the completion."""

    request_id: str
    model_alias: str
    provider: str
    input_tokens: int
    output_tokens: int
    charged_micro: int
    latency_ms: int
    status: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class FeedbackLine:
    id: str
    page: str
    message: str
    created_at: datetime


class DashboardStore(Protocol):
    """What the dashboard needs from storage. No writes to money, ever."""

    async def ensure_wallet(self, account_id: str) -> None:
        """Create the empty wallet a new account needs. Idempotent."""

    async def balance_micro(self, account_id: str) -> int: ...

    async def ledger(self, account_id: str, limit: int = 20) -> tuple[LedgerLine, ...]: ...

    async def usage(self, account_id: str, limit: int = 50) -> tuple[UsageLine, ...]: ...

    async def add_feedback(
        self, *, account_id: str | None, email: str | None, page: str, message: str
    ) -> None: ...

    async def feedback(self, limit: int = 50) -> tuple[FeedbackLine, ...]:
        """Most recent first. Used by the admin screen."""
