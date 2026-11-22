"""PostgreSQL reads for the dashboard, plus the two console writes that are not money."""

from __future__ import annotations

import uuid

from sqlalchemy import select, text

from tokn_core.dashboard import FeedbackLine, LedgerLine, UsageLine
from tokn_infra.db.models import Feedback, LedgerEntry, UsageEvent
from tokn_infra.postgres.session import SessionFactory


class PostgresDashboardStore:
    def __init__(self, sessions: SessionFactory) -> None:
        self._sessions = sessions

    async def ensure_wallet(self, account_id: str) -> None:
        async with self._sessions() as session, session.begin():
            await session.execute(
                text(
                    "INSERT INTO wallets (account_id, balance_micro) VALUES (:account_id, 0) "
                    "ON CONFLICT (account_id) DO NOTHING"
                ),
                {"account_id": _uuid(account_id)},
            )

    async def balance_micro(self, account_id: str) -> int:
        async with self._sessions() as session:
            value = await session.scalar(
                text("SELECT balance_micro FROM wallets WHERE account_id = :account_id"),
                {"account_id": _uuid(account_id)},
            )
            return int(value or 0)

    async def ledger(self, account_id: str, limit: int = 20) -> tuple[LedgerLine, ...]:
        async with self._sessions() as session:
            rows = await session.scalars(
                select(LedgerEntry)
                .where(LedgerEntry.account_id == _uuid(account_id))
                .order_by(LedgerEntry.created_at.desc())
                .limit(limit)
            )
            return tuple(
                LedgerLine(
                    kind=row.kind,
                    amount_micro=row.amount_micro,
                    balance_after_micro=row.balance_after_micro,
                    reference=row.reference,
                    created_at=row.created_at,
                )
                for row in rows
            )

    async def usage(self, account_id: str, limit: int = 50) -> tuple[UsageLine, ...]:
        async with self._sessions() as session:
            rows = await session.scalars(
                select(UsageEvent)
                .where(UsageEvent.account_id == _uuid(account_id))
                .order_by(UsageEvent.created_at.desc())
                .limit(limit)
            )
            return tuple(
                UsageLine(
                    request_id=row.request_id,
                    model_alias=row.model_alias,
                    provider=row.provider,
                    input_tokens=row.input_tokens,
                    output_tokens=row.output_tokens,
                    charged_micro=row.charged_micro,
                    latency_ms=row.latency_ms,
                    status=row.status,
                    created_at=row.created_at,
                )
                for row in rows
            )

    async def add_feedback(
        self, *, account_id: str | None, email: str | None, page: str, message: str
    ) -> None:
        async with self._sessions() as session, session.begin():
            session.add(
                Feedback(
                    account_id=None if account_id is None else _uuid(account_id),
                    email=email,
                    page=page[:120],
                    message=message[:4000],
                )
            )

    async def feedback(self, limit: int = 50) -> tuple[FeedbackLine, ...]:
        async with self._sessions() as session:
            rows = await session.scalars(
                select(Feedback).order_by(Feedback.created_at.desc()).limit(limit)
            )
            return tuple(
                FeedbackLine(
                    id=str(row.id),
                    page=row.page,
                    message=row.message,
                    created_at=row.created_at,
                )
                for row in rows
            )


def _uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as error:
        raise ValueError(f"not a UUID: {value}") from error
