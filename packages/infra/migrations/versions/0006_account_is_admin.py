"""accounts.is_admin

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-22

The admin settings screen needs to know who may open it. Role separation for the console
proper stays out of scope; this one flag guards the screen that can take the site offline.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "accounts",
        sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.create_index("ix_accounts_is_admin", "accounts", ["is_admin"])


def downgrade() -> None:
    op.drop_index("ix_accounts_is_admin", table_name="accounts")
    op.drop_column("accounts", "is_admin")
