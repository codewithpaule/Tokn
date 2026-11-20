"""The wallet aggregate.

A wallet holds a balance of exactly-spendable naira. Every mutation goes through a method
that refuses to leave the balance negative, because that check is the last line of defence
behind the database's ``CHECK (balance_micro >= 0)``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from tokn_core.errors import InsufficientFundsError, InvariantViolationError
from tokn_core.money import Naira


def _require_non_negative(amount: Naira, name: str) -> None:
    if amount.is_negative():
        raise InvariantViolationError(f"{name} must not be negative: {amount.format()}")


@dataclass(slots=True)
class Wallet:
    """A customer's prepaid balance, shared across every provider they may use."""

    balance: Naira = field(default_factory=Naira.zero)

    def __post_init__(self) -> None:
        if self.balance.is_negative():
            raise InvariantViolationError("a wallet balance can never be negative")

    def hold(self, amount: Naira) -> None:
        """Set aside the worst-case cost of one request.

        The database does this atomically with a conditional UPDATE; this method is the
        same rule expressed once, so tests and the in-memory adapter agree.
        """
        _require_non_negative(amount, "hold")
        if amount.micro > self.balance.micro:
            raise InsufficientFundsError(required=amount, available=self.balance)
        self.balance = self.balance - amount

    def settle(self, hold: Naira, charge: Naira) -> Naira:
        """Turn a hold into an actual charge and return the released remainder."""
        _require_non_negative(hold, "hold")
        _require_non_negative(charge, "charge")
        if charge.micro > hold.micro:
            raise InvariantViolationError(f"charge {charge.format()} exceeds hold {hold.format()}")
        refund = hold - charge
        self.balance = self.balance + refund
        return refund

    def release(self, hold: Naira) -> None:
        """Give back a hold that produced no charge, for example after an upstream error."""
        _require_non_negative(hold, "hold")
        self.balance = self.balance + hold

    def credit(self, amount: Naira) -> None:
        """Add funds, for example from a verified top-up."""
        _require_non_negative(amount, "credit")
        self.balance = self.balance + amount

    def debit(self, amount: Naira) -> None:
        """Remove funds outside a reservation, for example a corrective adjustment."""
        _require_non_negative(amount, "debit")
        if amount.micro > self.balance.micro:
            raise InsufficientFundsError(required=amount, available=self.balance)
        self.balance = self.balance - amount
