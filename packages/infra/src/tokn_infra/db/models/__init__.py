"""SQLAlchemy models.

Importing this package populates ``Base.metadata``, which is what Alembic autogenerate
and the offline SQL render both read.
"""

from tokn_infra.db.base import Base, TimestampMixin
from tokn_infra.db.models.auth import AuthSession, AuthToken, RecoveryCode
from tokn_infra.db.models.catalog import FxRate, ModelCatalog, ModelPrice, ProviderAccount
from tokn_infra.db.models.console import Feedback
from tokn_infra.db.models.identity import Account, AccountStatus, ApiKey
from tokn_infra.db.models.messaging import EmailOutbox
from tokn_infra.db.models.money import LedgerEntry, Reservation, UsageEvent, Wallet

__all__ = [
    "Account",
    "AccountStatus",
    "ApiKey",
    "AuthSession",
    "AuthToken",
    "Base",
    "EmailOutbox",
    "Feedback",
    "FxRate",
    "LedgerEntry",
    "ModelCatalog",
    "ModelPrice",
    "ProviderAccount",
    "RecoveryCode",
    "Reservation",
    "TimestampMixin",
    "UsageEvent",
    "Wallet",
]
