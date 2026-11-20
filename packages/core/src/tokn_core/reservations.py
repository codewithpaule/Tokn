"""Reservation state machine.

One reservation per request id, and exactly one transition out of ``held``:

    held ──settle(charge)──▶ settled      (charge <= hold, remainder released)
      └────release()───────▶ released     (nothing charged)

Settlement is idempotent on the request id. A client that disconnects mid-stream still
settles exactly once, and a duplicate callback from an upstream cannot charge twice.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from tokn_core.errors import InvariantViolationError
from tokn_core.money import Naira


class ReservationStatus(StrEnum):
    HELD = "held"
    SETTLED = "settled"
    RELEASED = "released"


@dataclass(slots=True)
class Reservation:
    """Credit held against one request while it is in flight."""

    request_id: str
    hold: Naira
    status: ReservationStatus = field(default=ReservationStatus.HELD)
    charge: Naira | None = field(default=None)

    def __post_init__(self) -> None:
        if not self.request_id:
            raise InvariantViolationError("a reservation needs a request id")
        if self.hold.is_negative():
            raise InvariantViolationError("a hold must not be negative")

    @property
    def is_open(self) -> bool:
        return self.status is ReservationStatus.HELD

    def settle(self, charge: Naira) -> Naira:
        """Settle with the real cost and return the amount released.

        Calling this twice with the same charge is a no-op that returns the same refund,
        which is what makes a retried settlement safe.
        """
        if charge.is_negative():
            raise InvariantViolationError("a charge must not be negative")
        if charge.micro > self.hold.micro:
            raise InvariantViolationError(
                f"charge {charge.format()} exceeds hold {self.hold.format()}"
            )
        if self.status is ReservationStatus.SETTLED:
            if self.charge != charge:
                raise InvariantViolationError(
                    f"reservation {self.request_id} already settled at "
                    f"{_format(self.charge)}, not {charge.format()}"
                )
            return self.hold - charge
        if self.status is ReservationStatus.RELEASED:
            raise InvariantViolationError(
                f"reservation {self.request_id} was released and cannot be settled"
            )
        self.status = ReservationStatus.SETTLED
        self.charge = charge
        return self.hold - charge

    def release(self) -> None:
        """Return the whole hold, for example when the upstream failed before any output."""
        if self.status is ReservationStatus.SETTLED:
            raise InvariantViolationError(
                f"reservation {self.request_id} is settled and cannot be released"
            )
        self.status = ReservationStatus.RELEASED
        self.charge = None


def _format(amount: Naira | None) -> str:
    return "nothing" if amount is None else amount.format()
