"""Exact money.

One naira is 1_000_000 micro-naira. Every amount in Tokn is an integer count of
micro-naira. No float ever touches a price, a balance or a ledger row.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from types import NotImplementedType

MICRO_PER_NAIRA: int = 1_000_000
_MICRO_QUANTUM = Decimal("0.000001")
_CENT_QUANTUM = Decimal("0.01")


@dataclass(frozen=True, slots=True, order=True)
class Naira:
    """An exact amount stored as integer micro-naira.

    Balances are never negative, but ledger deltas are signed, so this type allows
    negative values. Enforcement of non-negativity belongs to the wallet aggregate.
    """

    micro: int

    def __post_init__(self) -> None:
        if isinstance(self.micro, bool) or not isinstance(self.micro, int):
            raise TypeError("Naira.micro must be an int, not a float or bool")

    @classmethod
    def zero(cls) -> Naira:
        return cls(0)

    @classmethod
    def from_naira(cls, amount: Decimal | int | str) -> Naira:
        """Convert a naira amount, rounding half-up to the nearest micro-naira."""
        value = amount if isinstance(amount, Decimal) else Decimal(amount)
        if not value.is_finite():
            raise ValueError("amount must be finite")
        micro = value.quantize(_MICRO_QUANTUM, rounding=ROUND_HALF_UP) * MICRO_PER_NAIRA
        return cls(int(micro))

    def to_naira(self) -> Decimal:
        return Decimal(self.micro) / MICRO_PER_NAIRA

    def is_zero(self) -> bool:
        return self.micro == 0

    def is_negative(self) -> bool:
        return self.micro < 0

    def format(self) -> str:
        """Render for humans: two decimals, thousands separators, naira sign."""
        amount = self.to_naira().quantize(_CENT_QUANTUM, rounding=ROUND_HALF_UP)
        sign = "-" if amount.is_signed() and not amount.is_zero() else ""
        return f"{sign}₦{abs(amount):,.2f}"

    def __add__(self, other: Naira) -> Naira | NotImplementedType:
        if not isinstance(other, Naira):
            return NotImplemented
        return Naira(self.micro + other.micro)

    def __sub__(self, other: Naira) -> Naira | NotImplementedType:
        if not isinstance(other, Naira):
            return NotImplemented
        return Naira(self.micro - other.micro)

    def __neg__(self) -> Naira:
        return Naira(-self.micro)

    def __abs__(self) -> Naira:
        return Naira(abs(self.micro))

    def __str__(self) -> str:
        return self.format()
