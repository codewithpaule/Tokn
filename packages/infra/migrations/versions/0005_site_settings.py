"""site_settings

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-21

One row. The operator edits it from admin; every page reads it.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "site_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("available", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "maintenance_message",
            sa.Text(),
            nullable=False,
            server_default="We are making a change. Back shortly.",
        ),
        sa.Column("name", sa.Text(), nullable=False, server_default="Tokn"),
        sa.Column("mark", sa.Text(), nullable=False, server_default="T"),
        sa.Column("tagline", sa.Text(), nullable=False, server_default=""),
        sa.Column("hero_body", sa.Text(), nullable=False, server_default=""),
        sa.Column("support_email", sa.Text(), nullable=False, server_default="support@tokn.ng"),
        sa.Column("color_ink", sa.Text(), nullable=False, server_default="#0e1210"),
        sa.Column("color_paper", sa.Text(), nullable=False, server_default="#fbfbf8"),
        sa.Column("color_accent", sa.Text(), nullable=False, server_default="#1e6b4b"),
        sa.Column("color_accent_deep", sa.Text(), nullable=False, server_default="#154d36"),
        sa.Column("color_highlight", sa.Text(), nullable=False, server_default="#e3b93c"),
        sa.Column("font", sa.Text(), nullable=False, server_default="montserrat"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("id = 1", name="singleton"),
    )
    op.execute("INSERT INTO site_settings (id) VALUES (1) ON CONFLICT (id) DO NOTHING")


def downgrade() -> None:
    op.drop_table("site_settings")
