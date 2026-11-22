"""site_settings: the Brewkeg palette and name

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-22

The singleton row was seeded with the old brand. This moves the untouched row to the blue
and green defaults, and leaves any value an operator has already chosen alone.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE site_settings SET "
            "name = CASE WHEN name = 'Tokn' THEN 'Brewkeg' ELSE name END, "
            "mark = CASE WHEN mark = 'T' THEN 'B' ELSE mark END, "
            "support_email = CASE WHEN support_email = 'support@tokn.ng' "
            "THEN 'support@brewkeg.dev' ELSE support_email END, "
            "color_ink = CASE WHEN color_ink = '#0e1210' THEN '#0E1A24' ELSE color_ink END, "
            "color_paper = CASE WHEN color_paper = '#fbfbf8' THEN '#F7F9FC' ELSE color_paper END, "
            "color_accent = CASE WHEN color_accent = '#1e6b4b' "
            "THEN '#1F6B4A' ELSE color_accent END, "
            "color_accent_deep = CASE WHEN color_accent_deep = '#154d36' "
            "THEN '#17513A' ELSE color_accent_deep END, "
            "color_highlight = CASE WHEN color_highlight = '#e3b93c' "
            "THEN '#17497F' ELSE color_highlight END, "
            "updated_at = now() "
            "WHERE id = 1"
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE site_settings SET "
            "name = CASE WHEN name = 'Brewkeg' THEN 'Tokn' ELSE name END, "
            "mark = CASE WHEN mark = 'B' THEN 'T' ELSE mark END, "
            "support_email = CASE WHEN support_email = 'support@brewkeg.dev' "
            "THEN 'support@tokn.ng' ELSE support_email END, "
            "color_ink = CASE WHEN color_ink = '#0E1A24' THEN '#0e1210' ELSE color_ink END, "
            "color_paper = CASE WHEN color_paper = '#F7F9FC' THEN '#fbfbf8' ELSE color_paper END, "
            "color_accent = CASE WHEN color_accent = '#1F6B4A' "
            "THEN '#1e6b4b' ELSE color_accent END, "
            "color_accent_deep = CASE WHEN color_accent_deep = '#17513A' "
            "THEN '#154d36' ELSE color_accent_deep END, "
            "color_highlight = CASE WHEN color_highlight = '#17497F' "
            "THEN '#e3b93c' ELSE color_highlight END, "
            "updated_at = now() "
            "WHERE id = 1"
        )
    )
