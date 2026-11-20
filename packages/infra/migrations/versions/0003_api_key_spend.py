"""api_keys.spent_micro

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-20

A key's spend cap needs a running total it can compare against without summing the whole
ledger on every request. Phase 4 increments this inside the same transaction that settles
a charge, so it can never drift from usage_events.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "api_keys",
        sa.Column("spent_micro", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
    )
    op.create_check_constraint("spent_non_negative", "api_keys", "spent_micro >= 0")


def downgrade() -> None:
    op.drop_constraint("ck_api_keys_spent_non_negative", "api_keys", type_="check")
    op.drop_column("api_keys", "spent_micro")
