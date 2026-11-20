"""PostgreSQL wallet: reserve, settle, release.

Every operation is one transaction. The hold is a conditional UPDATE, which is what makes
concurrency safe without a read-modify-write:

    UPDATE wallets SET balance_micro = balance_micro - :hold
     WHERE account_id = :id AND balance_micro >= :hold

Zero rows updated means the balance could not cover it. Settlement locks the reservation
row, so a retry cannot charge twice, and writes exactly one ledger entry per request, keyed
by the request id.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from tokn_core.errors import InvariantViolationError, UnknownReservationError
from tokn_core.money import Naira
from tokn_infra.db.models import LedgerEntry, Reservation, Wallet
from tokn_infra.postgres.session import SessionFactory


class PostgresWalletStore:
    def __init__(self, sessions: SessionFactory) -> None:
        self._sessions = sessions

    async def reserve(self, *, request_id: str, account_id: str, key_id: str, hold: Naira) -> bool:
        if hold.is_negative():
            raise InvariantViolationError("a hold must not be negative")
        async with self._sessions() as session, session.begin():
            held = await session.scalar(
                update(Wallet)
                .where(Wallet.account_id == _uuid(account_id), Wallet.balance_micro >= hold.micro)
                .values(balance_micro=Wallet.balance_micro - hold.micro)
                .returning(Wallet.balance_micro)
            )
            if held is None:
                return False
            session.add(
                Reservation(
                    request_id=request_id,
                    account_id=_uuid(account_id),
                    key_id=_uuid(key_id),
                    hold_micro=hold.micro,
                    status="held",
                )
            )
            return True

    async def settle(self, *, request_id: str, charge: Naira) -> Naira:
        """Charge the real cost and release the rest. Returns what was charged."""
        if charge.is_negative():
            raise InvariantViolationError("a charge must not be negative")
        async with self._sessions() as session, session.begin():
            reservation = await self._locked(session, request_id)
            if reservation.status != "held":
                return Naira(reservation.charge_micro or 0)

            charged = min(charge.micro, reservation.hold_micro)
            refund = reservation.hold_micro - charged
            balance = await session.scalar(
                update(Wallet)
                .where(Wallet.account_id == reservation.account_id)
                .values(balance_micro=Wallet.balance_micro + refund)
                .returning(Wallet.balance_micro)
            )
            if charged > 0:
                session.add(
                    LedgerEntry(
                        account_id=reservation.account_id,
                        kind="usage",
                        amount_micro=-charged,
                        balance_after_micro=int(balance or 0),
                        reference=request_id,
                    )
                )
            reservation.status = "settled"
            reservation.charge_micro = charged
            reservation.settled_at = func.now()
            return Naira(charged)

    async def release(self, *, request_id: str) -> None:
        async with self._sessions() as session, session.begin():
            reservation = await self._locked(session, request_id)
            if reservation.status != "held":
                return
            await session.execute(
                update(Wallet)
                .where(Wallet.account_id == reservation.account_id)
                .values(balance_micro=Wallet.balance_micro + reservation.hold_micro)
            )
            reservation.status = "released"
            reservation.settled_at = func.now()

    async def balance(self, account_id: str) -> Naira:
        async with self._sessions() as session:
            value = await session.scalar(
                select(Wallet.balance_micro).where(Wallet.account_id == _uuid(account_id))
            )
            return Naira(int(value or 0))

    async def credit(
        self,
        *,
        account_id: str,
        amount: Naira,
        reference: str,
        kind: str = "topup",
        now: datetime | None = None,
    ) -> None:
        """Add funds and record the ledger line in the same transaction."""
        if amount.is_negative() or amount.is_zero():
            raise InvariantViolationError("a credit must be positive")
        async with self._sessions() as session, session.begin():
            balance = await session.scalar(
                update(Wallet)
                .where(Wallet.account_id == _uuid(account_id))
                .values(balance_micro=Wallet.balance_micro + amount.micro)
                .returning(Wallet.balance_micro)
            )
            if balance is None:
                raise InvariantViolationError("no wallet for that account")
            session.add(
                LedgerEntry(
                    account_id=_uuid(account_id),
                    kind=kind,
                    amount_micro=amount.micro,
                    balance_after_micro=int(balance),
                    reference=reference,
                )
            )

    @staticmethod
    async def _locked(session: AsyncSession, request_id: str) -> Reservation:
        reservation: Reservation | None = await session.scalar(
            select(Reservation).where(Reservation.request_id == request_id).with_for_update()
        )
        if reservation is None:
            raise UnknownReservationError(request_id)
        return reservation


def _uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as error:
        raise ValueError(f"not a UUID: {value}") from error
