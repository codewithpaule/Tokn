"""feedback

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-22

The dashboard's suggestion box needs somewhere durable to put what people write. No
foreign key cascade to the account: deleting an account should not silently erase a bug
report we may still need to act on.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "feedback",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("account_id", sa.UUID(), nullable=True),
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column("page", sa.Text(), nullable=False, server_default="dashboard"),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
        sa.ForeignKeyConstraint(
            ["account_id"], ["accounts.id"], name="fk_feedback_account_id_accounts",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_feedback"),
    )
    op.create_index("ix_feedback_created", "feedback", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_feedback_created", table_name="feedback")
    op.drop_table("feedback")
