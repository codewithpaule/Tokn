"""Accounts and their API keys.

An account owns exactly one wallet. API keys are stored only as an HMAC-SHA256 hash:
a key that leaks from this database is still useless to an attacker.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, Text
from sqlalchemy.dialects.postgresql import ARRAY, CITEXT, UUID
from sqlalchemy.orm import Mapped, mapped_column

from tokn_infra.db.base import Base, TimestampMixin


class AccountStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    CLOSED = "closed"


class Account(TimestampMixin, Base):
    __tablename__ = "accounts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(CITEXT, nullable=False, unique=True)
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    email_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    totp_secret_enc: Mapped[bytes | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=AccountStatus.PENDING.value
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'active', 'suspended', 'closed')",
            name="status_known",
        ),
    )


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # The public, non-secret part of the key. Unique so GitHub secret scanning can find it.
    prefix: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    key_hash: Mapped[bytes] = mapped_column(nullable=False, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    scopes: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    spend_cap_micro: Mapped[int | None] = mapped_column(nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ip_allowlist: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
    auto_paused: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    concurrency_limit: Mapped[int] = mapped_column(Integer, nullable=False, server_default="8")

    __table_args__ = (
        CheckConstraint(
            "spend_cap_micro IS NULL OR spend_cap_micro >= 0", name="spend_cap_non_negative"
        ),
        CheckConstraint("concurrency_limit > 0", name="concurrency_positive"),
    )
