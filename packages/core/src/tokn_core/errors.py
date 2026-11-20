"""Domain errors.

These are the only failures the money layer raises. Transport code maps them to HTTP
status codes; the domain never knows about HTTP.
"""

from __future__ import annotations

from tokn_core.money import Naira


class DomainError(Exception):
    """Base class for every rule violation in the domain."""


class InvalidAmountError(DomainError):
    """An amount that cannot exist: negative tokens, negative price, bad rate."""


class InsufficientFundsError(DomainError):
    """The wallet cannot cover the requested amount."""

    def __init__(self, required: Naira, available: Naira) -> None:
        self.required = required
        self.available = available
        super().__init__(
            f"insufficient funds: need {required.format()}, available {available.format()}"
        )


class InvariantViolationError(DomainError):
    """A ledger, wallet or reservation rule was about to be broken."""


class UnknownReservationError(DomainError):
    """A request id has no reservation, so there is nothing to settle."""


class AccountExistsError(DomainError):
    """An account with that email is already registered."""


class AuthenticationFailedError(DomainError):
    """Credentials did not match. Deliberately carries no detail about why."""
