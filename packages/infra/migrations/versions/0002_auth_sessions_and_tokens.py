"""sessions, auth tokens and recovery codes

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-20

Phase 1 created the money schema before authentication existed. These three tables hold
only keyed digests, never the values a user presents.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NOW = sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("digest", sa.Text(), nullable=False, unique=True),
        sa.Column(
            "account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("accounts.id", ondelete="CASCADE", name="fk_sessions_account_id_accounts"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("ip", sa.Text(), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
    )
    op.create_index("ix_sessions_account", "sessions", ["account_id"])

    op.create_table(
        "auth_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "accounts.id", ondelete="CASCADE", name="fk_auth_tokens_account_id_accounts"
            ),
            nullable=False,
        ),
        sa.Column("digest", sa.Text(), nullable=False, unique=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.CheckConstraint(
            "kind IN ('email_verification', 'password_reset')",
            name="kind_known",
        ),
    )
    op.create_index("ix_auth_tokens_account_kind", "auth_tokens", ["account_id", "kind"])

    op.create_table(
        "recovery_codes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "accounts.id", ondelete="CASCADE", name="fk_recovery_codes_account_id_accounts"
            ),
            nullable=False,
        ),
        sa.Column("digest", sa.Text(), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("account_id", "digest", name="uq_recovery_codes_account_digest"),
    )
    op.create_index("ix_recovery_codes_account", "recovery_codes", ["account_id"])


def downgrade() -> None:
    op.drop_index("ix_recovery_codes_account", table_name="recovery_codes")
    op.drop_table("recovery_codes")
    op.drop_index("ix_auth_tokens_account_kind", table_name="auth_tokens")
    op.drop_table("auth_tokens")
    op.drop_index("ix_sessions_account", table_name="sessions")
    op.drop_table("sessions")
