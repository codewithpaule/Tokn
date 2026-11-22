"""In-process dashboard store for development and tests.

It mirrors the read model, not the money rules. A balance here is only ever zero until
something explicitly credits it, which is exactly what a developer preview needs: the
dashboard is honest about an empty wallet instead of inventing a number.
"""

from __future__ import annotations

from datetime import UTC, datetime

from tokn_core.dashboard import FeedbackLine, LedgerLine, UsageLine


class InMemoryDashboardStore:
    def __init__(self) -> None:
        self._balances: dict[str, int] = {}
        self._ledger: dict[str, list[LedgerLine]] = {}
        self._usage: dict[str, list[UsageLine]] = {}
        self._feedback: list[FeedbackLine] = []
        self._next_feedback = 0

    async def ensure_wallet(self, account_id: str) -> None:
        self._balances.setdefault(account_id, 0)

    async def balance_micro(self, account_id: str) -> int:
        return self._balances.get(account_id, 0)

    async def ledger(self, account_id: str, limit: int = 20) -> tuple[LedgerLine, ...]:
        return tuple(self._ledger.get(account_id, [])[:limit])

    async def usage(self, account_id: str, limit: int = 50) -> tuple[UsageLine, ...]:
        return tuple(self._usage.get(account_id, [])[:limit])

    async def add_feedback(
        self, *, account_id: str | None, email: str | None, page: str, message: str
    ) -> None:
        self._next_feedback += 1
        self._feedback.insert(
            0,
            FeedbackLine(
                id=f"fb_{self._next_feedback}",
                page=page[:120],
                message=message[:4000],
                created_at=datetime.now(tz=UTC),
            ),
        )

    async def feedback(self, limit: int = 50) -> tuple[FeedbackLine, ...]:
        return tuple(self._feedback[:limit])

    # -- development helpers, never called by the console -----------------

    def credit(self, account_id: str, amount_micro: int, reference: str = "dev-topup") -> None:
        balance = self._balances.get(account_id, 0) + amount_micro
        self._balances[account_id] = balance
        self._ledger.setdefault(account_id, []).insert(
            0,
            LedgerLine(
                kind="topup",
                amount_micro=amount_micro,
                balance_after_micro=balance,
                reference=reference,
                created_at=datetime.now(tz=UTC),
            ),
        )
