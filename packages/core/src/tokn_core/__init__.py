"""Tokn domain core.

Framework-free models, ports and use cases. This package must never import
FastAPI, SQLAlchemy, Redis, httpx or any application package.
"""

from tokn_core.errors import (
    DomainError,
    InsufficientFundsError,
    InvalidAmountError,
    InvariantViolationError,
    UnknownReservationError,
)
from tokn_core.ledger import EntryKind, Ledger, LedgerEntry
from tokn_core.money import MICRO_PER_NAIRA, Naira
from tokn_core.pricing import BPS, MILLION, FxRate, PriceSchedule, TokenUsage, ceil_div
from tokn_core.reservations import Reservation, ReservationStatus
from tokn_core.wallet import Wallet

__all__ = [
    "BPS",
    "MICRO_PER_NAIRA",
    "MILLION",
    "DomainError",
    "EntryKind",
    "FxRate",
    "InsufficientFundsError",
    "InvalidAmountError",
    "InvariantViolationError",
    "Ledger",
    "LedgerEntry",
    "Naira",
    "PriceSchedule",
    "Reservation",
    "ReservationStatus",
    "TokenUsage",
    "UnknownReservationError",
    "Wallet",
    "ceil_div",
]
__version__ = "0.1.0"
