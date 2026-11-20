"""Pricing.

Prices live in the model catalog as integer micro-naira per million tokens. Charging is
exact integer arithmetic and rounds up exactly once, at the end, so a request can never
be undercharged and the ledger never sees a fraction.

    charge = ceil( sum(tokens_i * price_i) * (1 + markup_bps/10000) / 1_000_000 )
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from tokn_core.errors import InvalidAmountError
from tokn_core.money import Naira

MILLION = 1_000_000
BPS = 10_000


def ceil_div(numerator: int, denominator: int) -> int:
    """Divide rounding up, for non-negative numerators and positive denominators."""
    if denominator <= 0:
        raise InvalidAmountError("denominator must be positive")
    return -((-numerator) // denominator)


def _require_count(value: int, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidAmountError(f"{field_name} must be an int")
    if value < 0:
        raise InvalidAmountError(f"{field_name} must not be negative")


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """What one request consumed. Counts only, never content."""

    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def __post_init__(self) -> None:
        _require_count(self.input_tokens, "input_tokens")
        _require_count(self.output_tokens, "output_tokens")
        _require_count(self.cache_read_tokens, "cache_read_tokens")
        _require_count(self.cache_write_tokens, "cache_write_tokens")


@dataclass(frozen=True, slots=True)
class PriceSchedule:
    """One version of a model's retail price, in micro-naira per million tokens."""

    input_per_million: Naira
    output_per_million: Naira
    cache_read_per_million: Naira = field(default_factory=Naira.zero)
    cache_write_per_million: Naira = field(default_factory=Naira.zero)
    markup_bps: int = 0

    def __post_init__(self) -> None:
        for name in (
            "input_per_million",
            "output_per_million",
            "cache_read_per_million",
            "cache_write_per_million",
        ):
            price: Naira = getattr(self, name)
            if price.is_negative():
                raise InvalidAmountError(f"{name} must not be negative")
        _require_count(self.markup_bps, "markup_bps")

    def upstream_cost(self, usage: TokenUsage) -> Naira:
        """What the upstream provider charges us, before markup."""
        return Naira(ceil_div(self._weighted(usage), MILLION))

    def charge(self, usage: TokenUsage) -> Naira:
        """What the customer pays: upstream cost plus markup, rounded up once."""
        weighted = self._weighted(usage) * (BPS + self.markup_bps)
        return Naira(ceil_div(weighted, MILLION * BPS))

    def hold(self, estimated_input_tokens: int, max_output_tokens: int) -> Naira:
        """The worst case we reserve before the request leaves our process."""
        return self.charge(TokenUsage(estimated_input_tokens, max_output_tokens))

    def _weighted(self, usage: TokenUsage) -> int:
        return (
            usage.input_tokens * self.input_per_million.micro
            + usage.output_tokens * self.output_per_million.micro
            + usage.cache_read_tokens * self.cache_read_per_million.micro
            + usage.cache_write_tokens * self.cache_write_per_million.micro
        )


@dataclass(frozen=True, slots=True)
class FxRate:
    """A recorded conversion from one currency into naira.

    ``micro_naira_per_unit`` is the rate itself, stored as an integer with six decimal
    places, so no float ever touches a conversion. One US dollar at 1,600 naira is
    1_600_000_000.
    """

    from_currency: str
    to_currency: str
    micro_naira_per_unit: int
    recorded_at: datetime

    def __post_init__(self) -> None:
        if not self.from_currency or not self.to_currency:
            raise InvalidAmountError("currency codes must not be empty")
        _require_count(self.micro_naira_per_unit, "micro_naira_per_unit")

    def to_naira(self, amount_micro: int) -> Naira:
        """Convert an amount in the foreign currency's micro-units into naira."""
        _require_count(amount_micro, "amount_micro")
        return Naira(ceil_div(amount_micro * self.micro_naira_per_unit, MILLION))
