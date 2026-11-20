"""Money tables: wallets, the append-only ledger, reservations and usage receipts.

Every amount is BIGINT micro-naira (ADR-0003). The constraints here are the real
enforcement: the application can be wrong, the database must not be.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from tokn_infra.db.base import Base


class Wallet(Base):
    """One prepaid wallet per account, shared across every provider."""

    __tablename__ = "wallets"

    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="RESTRICT"), primary_key=True
    )
    balance_micro: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (CheckConstraint("balance_micro >= 0", name="balance_non_negative"),)


class LedgerEntry(Base):
    """Append-only. The application role holds INSERT and SELECT, nothing else."""

    __tablename__ = "ledger_entries"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    amount_micro: Mapped[int] = mapped_column(BigInteger, nullable=False)
    balance_after_micro: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reference: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("kind IN ('topup', 'usage', 'refund', 'adjustment')", name="kind_known"),
        CheckConstraint("balance_after_micro >= 0", name="balance_after_non_negative"),
        CheckConstraint("amount_micro <> 0", name="amount_non_zero"),
        Index("ix_ledger_entries_account_created", "account_id", "created_at"),
    )


class Reservation(Base):
    """Credit held for one in-flight request. Exactly one terminal transition."""

    __tablename__ = "reservations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    request_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False
    )
    key_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("api_keys.id", ondelete="RESTRICT"), nullable=False
    )
    hold_micro: Mapped[int] = mapped_column(BigInteger, nullable=False)
    charge_micro: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="held")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint("hold_micro >= 0", name="hold_non_negative"),
        CheckConstraint("status IN ('held', 'settled', 'released')", name="status_known"),
        CheckConstraint(
            "charge_micro IS NULL OR charge_micro <= hold_micro", name="charge_within_hold"
        ),
        CheckConstraint(
            "(status = 'settled') = (charge_micro IS NOT NULL)", name="charge_iff_settled"
        ),
        Index("ix_reservations_open", "status", "created_at"),
    )


class UsageEvent(Base):
    """The receipt. Metadata only: tokens, cost, latency, outcome. No content, ever."""

    __tablename__ = "usage_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    request_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False
    )
    key_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("api_keys.id", ondelete="RESTRICT"), nullable=False
    )
    model_alias: Mapped[str] = mapped_column(Text, nullable=False)
    upstream_model: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    cache_read_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    cache_write_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    upstream_cost_micro: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    charged_micro: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    status: Mapped[str] = mapped_column(Text, nullable=False)
    flagged_for_review: Mapped[bool] = mapped_column(nullable=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("input_tokens >= 0", name="input_tokens_non_negative"),
        CheckConstraint("output_tokens >= 0", name="output_tokens_non_negative"),
        CheckConstraint("cache_read_tokens >= 0", name="cache_read_non_negative"),
        CheckConstraint("cache_write_tokens >= 0", name="cache_write_non_negative"),
        CheckConstraint("upstream_cost_micro >= 0", name="upstream_cost_non_negative"),
        CheckConstraint("charged_micro >= 0", name="charged_non_negative"),
        CheckConstraint("latency_ms >= 0", name="latency_non_negative"),
        CheckConstraint(
            "status IN ('ok', 'stream_error', 'upstream_error', 'client_disconnect', "
            "'usage_missing')",
            name="status_known",
        ),
        Index("ix_usage_events_account_created", "account_id", "created_at"),
        Index("ix_usage_events_provider_created", "provider", "created_at"),
    )
