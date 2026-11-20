"""The ledger.

Append-only. Every entry records the signed amount and the balance immediately after it,
so a balance can always be rebuilt by replaying history and checked against the stored
value. Nothing here can modify or delete an entry.

The nightly reconciliation job asks one question: does the sum of entries equal the
wallet balance? This module is the in-memory form of that same invariant.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from tokn_core.errors import InvariantViolationError
from tokn_core.money import Naira


class EntryKind(StrEnum):
    TOPUP = "topup"
    USAGE = "usage"
    REFUND = "refund"
    ADJUSTMENT = "adjustment"


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    """One immutable line of accounting. Signed: credits positive, charges negative."""

    kind: EntryKind
    amount: Naira
    balance_after: Naira
    reference: str

    def __post_init__(self) -> None:
        if not self.reference:
            raise InvariantViolationError("a ledger entry needs a reference")
        if self.balance_after.is_negative():
            raise InvariantViolationError("balance_after must not be negative")


class Ledger:
    """An append-only sequence of entries for one account."""

    def __init__(self) -> None:
        self._entries: list[LedgerEntry] = []
        self._references: set[str] = set()
        self._balance = Naira.zero()

    @property
    def balance(self) -> Naira:
        return self._balance

    @property
    def entries(self) -> tuple[LedgerEntry, ...]:
        return tuple(self._entries)

    def append(self, kind: EntryKind, amount: Naira, reference: str) -> LedgerEntry:
        """Record one entry, refusing duplicates and any negative resulting balance."""
        if reference in self._references:
            raise InvariantViolationError(f"reference already recorded: {reference}")
        balance_after = self._balance + amount
        entry = LedgerEntry(
            kind=kind,
            amount=amount,
            balance_after=balance_after,
            reference=reference,
        )
        self._entries.append(entry)
        self._references.add(reference)
        self._balance = balance_after
        return entry

    def total(self) -> Naira:
        """Sum of every signed amount. Must equal :attr:`balance`."""
        running = Naira.zero()
        for entry in self._entries:
            running = running + entry.amount
        return running

    def is_consistent(self) -> bool:
        """True when replaying the entries reproduces the current balance."""
        return self.total().micro == self._balance.micro
