"""Model catalog, price versions, FX records and the provider treasury.

Prices are versioned rather than edited in place: a receipt must still be explainable a
year later, against the price that was in force when the request ran.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from tokn_infra.db.base import Base, TimestampMixin


class ModelCatalog(TimestampMixin, Base):
    """One public alias, resolved to one upstream model on one provider."""

    __tablename__ = "model_catalog"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    alias: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    upstream_model: Mapped[str] = mapped_column(Text, nullable=False)
    protocol: Mapped[str] = mapped_column(Text, nullable=False, server_default="openai")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    max_context_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    max_output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    fallback_alias: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint("protocol IN ('openai', 'anthropic', 'gemini')", name="protocol_known"),
        CheckConstraint("max_context_tokens >= 0", name="max_context_non_negative"),
        CheckConstraint("max_output_tokens >= 0", name="max_output_non_negative"),
    )


class ModelPrice(Base):
    """A price version. Effective from a timestamp; never edited after it is in use."""

    __tablename__ = "model_prices"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    catalog_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("model_catalog.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    currency: Mapped[str] = mapped_column(Text, nullable=False, server_default="NGN")
    input_per_million_micro: Mapped[int] = mapped_column(BigInteger, nullable=False)
    output_per_million_micro: Mapped[int] = mapped_column(BigInteger, nullable=False)
    cache_read_per_million_micro: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default="0"
    )
    cache_write_per_million_micro: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default="0"
    )
    markup_bps: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    effective_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("catalog_id", "effective_from", name="catalog_effective"),
        CheckConstraint("input_per_million_micro >= 0", name="input_non_negative"),
        CheckConstraint("output_per_million_micro >= 0", name="output_non_negative"),
        CheckConstraint("cache_read_per_million_micro >= 0", name="cache_read_non_negative"),
        CheckConstraint("cache_write_per_million_micro >= 0", name="cache_write_non_negative"),
        CheckConstraint("markup_bps >= 0", name="markup_non_negative"),
    )


class FxRate(Base):
    """A recorded conversion rate. ``micro_naira_per_unit`` has six decimal places."""

    __tablename__ = "fx_rates"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    from_currency: Mapped[str] = mapped_column(Text, nullable=False)
    to_currency: Mapped[str] = mapped_column(Text, nullable=False, server_default="NGN")
    micro_naira_per_unit: Mapped[int] = mapped_column(BigInteger, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("from_currency", "to_currency", "recorded_at", name="currency_pair_at"),
        CheckConstraint("micro_naira_per_unit > 0", name="rate_positive"),
    )


class ProviderAccount(TimestampMixin, Base):
    """The operator's own upstream billing relationship, never a customer wallet."""

    __tablename__ = "provider_accounts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    billing_mode: Mapped[str] = mapped_column(Text, nullable=False)
    currency: Mapped[str] = mapped_column(Text, nullable=False)
    low_balance_threshold_micro: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default="0"
    )
    last_verified_balance_micro: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="unknown")

    __table_args__ = (
        CheckConstraint(
            "billing_mode IN ('prepaid', 'postpaid', 'other')", name="billing_mode_known"
        ),
        CheckConstraint(
            "status IN ('healthy', 'low', 'depleted', 'disabled', 'unknown')",
            name="status_known",
        ),
        CheckConstraint("low_balance_threshold_micro >= 0", name="threshold_non_negative"),
        CheckConstraint(
            "last_verified_balance_micro IS NULL OR last_verified_balance_micro >= 0",
            name="balance_non_negative",
        ),
    )
