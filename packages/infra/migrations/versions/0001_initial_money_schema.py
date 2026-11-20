"""initial money schema

Revision ID: 0001
Revises:
Create Date: 2026-11-20

Accounts, API keys, one wallet per account, the append-only ledger, reservations, usage
receipts, the versioned model catalog, FX records and the provider treasury.

Money is BIGINT micro-naira (ADR-0003). Constraints carry the invariants; the
application is not trusted to be the only guard.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NOW = sa.text("now()")


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")

    op.create_table(
        "accounts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", postgresql.CITEXT(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=True),
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("totp_secret_enc", sa.LargeBinary(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.UniqueConstraint("email", name="uq_accounts_email"),
        sa.CheckConstraint(
            "status IN ('pending', 'active', 'suspended', 'closed')",
            name="status_known",
        ),
    )

    op.create_table(
        "api_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "accounts.id", ondelete="CASCADE", name="fk_api_keys_account_id_accounts"
            ),
            nullable=False,
        ),
        sa.Column("prefix", sa.Text(), nullable=False),
        sa.Column("key_hash", sa.LargeBinary(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("scopes", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("spend_cap_micro", sa.BigInteger(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ip_allowlist", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("auto_paused", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("concurrency_limit", sa.Integer(), nullable=False, server_default=sa.text("8")),
        sa.UniqueConstraint("prefix", name="uq_api_keys_prefix"),
        sa.UniqueConstraint("key_hash", name="uq_api_keys_key_hash"),
        sa.CheckConstraint(
            "spend_cap_micro IS NULL OR spend_cap_micro >= 0",
            name="spend_cap_non_negative",
        ),
        sa.CheckConstraint("concurrency_limit > 0", name="concurrency_positive"),
    )
    op.create_index("ix_api_keys_account_id", "api_keys", ["account_id"])

    op.create_table(
        "wallets",
        sa.Column(
            "account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "accounts.id", ondelete="RESTRICT", name="fk_wallets_account_id_accounts"
            ),
            primary_key=True,
        ),
        sa.Column("balance_micro", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.CheckConstraint("balance_micro >= 0", name="balance_non_negative"),
    )

    op.create_table(
        "ledger_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "accounts.id", ondelete="RESTRICT", name="fk_ledger_entries_account_id_accounts"
            ),
            nullable=False,
        ),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("amount_micro", sa.BigInteger(), nullable=False),
        sa.Column("balance_after_micro", sa.BigInteger(), nullable=False),
        sa.Column("reference", sa.Text(), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.CheckConstraint(
            "kind IN ('topup', 'usage', 'refund', 'adjustment')",
            name="kind_known",
        ),
        sa.CheckConstraint("balance_after_micro >= 0", name="balance_after_non_negative"),
        sa.CheckConstraint("amount_micro <> 0", name="amount_non_zero"),
    )
    op.create_index(
        "ix_ledger_entries_account_created", "ledger_entries", ["account_id", "created_at"]
    )

    op.create_table(
        "reservations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("request_id", sa.Text(), nullable=False, unique=True),
        sa.Column(
            "account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "accounts.id", ondelete="RESTRICT", name="fk_reservations_account_id_accounts"
            ),
            nullable=False,
        ),
        sa.Column(
            "key_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "api_keys.id", ondelete="RESTRICT", name="fk_reservations_key_id_api_keys"
            ),
            nullable=False,
        ),
        sa.Column("hold_micro", sa.BigInteger(), nullable=False),
        sa.Column("charge_micro", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="held"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("hold_micro >= 0", name="hold_non_negative"),
        sa.CheckConstraint("status IN ('held', 'settled', 'released')", name="status_known"),
        sa.CheckConstraint(
            "charge_micro IS NULL OR charge_micro <= hold_micro",
            name="charge_within_hold",
        ),
        sa.CheckConstraint(
            "(status = 'settled') = (charge_micro IS NOT NULL)",
            name="charge_iff_settled",
        ),
    )
    op.create_index("ix_reservations_open", "reservations", ["status", "created_at"])

    op.create_table(
        "usage_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("request_id", sa.Text(), nullable=False, unique=True),
        sa.Column(
            "account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "accounts.id", ondelete="RESTRICT", name="fk_usage_events_account_id_accounts"
            ),
            nullable=False,
        ),
        sa.Column(
            "key_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "api_keys.id", ondelete="RESTRICT", name="fk_usage_events_key_id_api_keys"
            ),
            nullable=False,
        ),
        sa.Column("model_alias", sa.Text(), nullable=False),
        sa.Column("upstream_model", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("cache_read_tokens", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("cache_write_tokens", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "upstream_cost_micro", sa.BigInteger(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("charged_micro", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "flagged_for_review", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.CheckConstraint("input_tokens >= 0", name="input_tokens_non_negative"),
        sa.CheckConstraint("output_tokens >= 0", name="output_tokens_non_negative"),
        sa.CheckConstraint("cache_read_tokens >= 0", name="cache_read_non_negative"),
        sa.CheckConstraint("cache_write_tokens >= 0", name="cache_write_non_negative"),
        sa.CheckConstraint("upstream_cost_micro >= 0", name="upstream_cost_non_negative"),
        sa.CheckConstraint("charged_micro >= 0", name="charged_non_negative"),
        sa.CheckConstraint("latency_ms >= 0", name="latency_non_negative"),
        sa.CheckConstraint(
            "status IN ('ok', 'stream_error', 'upstream_error', 'client_disconnect', "
            "'usage_missing')",
            name="status_known",
        ),
    )
    op.create_index("ix_usage_events_account_created", "usage_events", ["account_id", "created_at"])
    op.create_index("ix_usage_events_provider_created", "usage_events", ["provider", "created_at"])

    op.create_table(
        "model_catalog",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("alias", sa.Text(), nullable=False, unique=True),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("upstream_model", sa.Text(), nullable=False),
        sa.Column("protocol", sa.Text(), nullable=False, server_default="openai"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("max_context_tokens", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("max_output_tokens", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("fallback_alias", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.CheckConstraint(
            "protocol IN ('openai', 'anthropic', 'gemini')",
            name="protocol_known",
        ),
        sa.CheckConstraint("max_context_tokens >= 0", name="max_context_non_negative"),
        sa.CheckConstraint("max_output_tokens >= 0", name="max_output_non_negative"),
    )

    op.create_table(
        "model_prices",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "catalog_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "model_catalog.id",
                ondelete="RESTRICT",
                name="fk_model_prices_catalog_id_model_catalog",
            ),
            nullable=False,
        ),
        sa.Column("currency", sa.Text(), nullable=False, server_default="NGN"),
        sa.Column("input_per_million_micro", sa.BigInteger(), nullable=False),
        sa.Column("output_per_million_micro", sa.BigInteger(), nullable=False),
        sa.Column(
            "cache_read_per_million_micro",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "cache_write_per_million_micro",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("markup_bps", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.UniqueConstraint(
            "catalog_id", "effective_from", name="uq_model_prices_catalog_effective"
        ),
        sa.CheckConstraint("input_per_million_micro >= 0", name="input_non_negative"),
        sa.CheckConstraint("output_per_million_micro >= 0", name="output_non_negative"),
        sa.CheckConstraint(
            "cache_read_per_million_micro >= 0",
            name="cache_read_non_negative",
        ),
        sa.CheckConstraint(
            "cache_write_per_million_micro >= 0",
            name="cache_write_non_negative",
        ),
        sa.CheckConstraint("markup_bps >= 0", name="markup_non_negative"),
    )
    op.create_index("ix_model_prices_catalog_id", "model_prices", ["catalog_id"])

    op.create_table(
        "fx_rates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("from_currency", sa.Text(), nullable=False),
        sa.Column("to_currency", sa.Text(), nullable=False, server_default="NGN"),
        sa.Column("micro_naira_per_unit", sa.BigInteger(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "from_currency", "to_currency", "recorded_at", name="uq_fx_rates_currency_pair_at"
        ),
        sa.CheckConstraint("micro_naira_per_unit > 0", name="rate_positive"),
    )

    op.create_table(
        "provider_accounts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("provider", sa.Text(), nullable=False, unique=True),
        sa.Column("billing_mode", sa.Text(), nullable=False),
        sa.Column("currency", sa.Text(), nullable=False),
        sa.Column(
            "low_balance_threshold_micro",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("last_verified_balance_micro", sa.BigInteger(), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="unknown"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.CheckConstraint(
            "billing_mode IN ('prepaid', 'postpaid', 'other')",
            name="billing_mode_known",
        ),
        sa.CheckConstraint(
            "status IN ('healthy', 'low', 'depleted', 'disabled', 'unknown')",
            name="status_known",
        ),
        sa.CheckConstraint(
            "low_balance_threshold_micro >= 0",
            name="threshold_non_negative",
        ),
        sa.CheckConstraint(
            "last_verified_balance_micro IS NULL OR last_verified_balance_micro >= 0",
            name="balance_non_negative",
        ),
    )


def downgrade() -> None:
    op.drop_table("provider_accounts")
    op.drop_table("fx_rates")
    op.drop_index("ix_model_prices_catalog_id", table_name="model_prices")
    op.drop_table("model_prices")
    op.drop_table("model_catalog")
    op.drop_index("ix_usage_events_provider_created", table_name="usage_events")
    op.drop_index("ix_usage_events_account_created", table_name="usage_events")
    op.drop_table("usage_events")
    op.drop_index("ix_reservations_open", table_name="reservations")
    op.drop_table("reservations")
    op.drop_index("ix_ledger_entries_account_created", table_name="ledger_entries")
    op.drop_table("ledger_entries")
    op.drop_table("wallets")
    op.drop_index("ix_api_keys_account_id", table_name="api_keys")
    op.drop_table("api_keys")
    op.drop_table("accounts")
    op.execute("DROP EXTENSION IF EXISTS citext")
