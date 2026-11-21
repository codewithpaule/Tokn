"""Where the site settings live, and a cache in front of them.

The row is read on nearly every request, so it is cached in process for 30 seconds and
invalidated on save. Losing the cache costs one query, never correctness.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import text

from tokn_core.site import SiteSettings
from tokn_infra.postgres.session import SessionFactory

TTL = timedelta(seconds=30)

SELECT_ONE = (
    "SELECT available, maintenance_message, name, mark, tagline, hero_body, "
    "support_email, color_ink, color_paper, color_accent, color_accent_deep, "
    "color_highlight, font FROM site_settings WHERE id = 1"
)


class SiteSettingsStore(Protocol):
    async def load(self) -> SiteSettings: ...

    async def save(self, settings: SiteSettings) -> None: ...


class InMemorySiteSettings:
    def __init__(self, settings: SiteSettings | None = None) -> None:
        self._settings = (settings or SiteSettings()).validated()

    async def load(self) -> SiteSettings:
        return self._settings

    async def save(self, settings: SiteSettings) -> None:
        self._settings = settings.validated()


class PostgresSiteSettings:
    def __init__(self, sessions: SessionFactory) -> None:
        self._sessions = sessions
        self._cached: SiteSettings | None = None
        self._fetched_at: datetime | None = None

    async def load(self) -> SiteSettings:
        now = datetime.now(tz=UTC)
        if (
            self._cached is not None
            and self._fetched_at is not None
            and now - self._fetched_at < TTL
        ):
            return self._cached

        async with self._sessions() as session:
            row = (await session.execute(text(SELECT_ONE))).mappings().one_or_none()

        settings = SiteSettings() if row is None else _from_row(row)
        self._cached = settings
        self._fetched_at = now
        return settings

    async def save(self, settings: SiteSettings) -> None:
        valid = settings.validated()
        async with self._sessions() as session, session.begin():
            await session.execute(
                text(
                    "UPDATE site_settings SET available = :available, "
                    "maintenance_message = :maintenance_message, name = :name, mark = :mark, "
                    "tagline = :tagline, hero_body = :hero_body, "
                    "support_email = :support_email, color_ink = :color_ink, "
                    "color_paper = :color_paper, color_accent = :color_accent, "
                    "color_accent_deep = :color_accent_deep, "
                    "color_highlight = :color_highlight, font = :font, "
                    "updated_at = now() WHERE id = 1"
                ),
                {
                    "available": valid.available,
                    "maintenance_message": valid.maintenance_message,
                    "name": valid.name,
                    "mark": valid.mark,
                    "tagline": valid.tagline,
                    "hero_body": valid.hero_body,
                    "support_email": valid.support_email,
                    "color_ink": valid.color_ink,
                    "color_paper": valid.color_paper,
                    "color_accent": valid.color_accent,
                    "color_accent_deep": valid.color_accent_deep,
                    "color_highlight": valid.color_highlight,
                    "font": valid.font,
                },
            )
        self._cached = valid
        self._fetched_at = datetime.now(tz=UTC)


def _from_row(row: Any) -> SiteSettings:
    return SiteSettings(
        available=bool(row["available"]),
        maintenance_message=str(row["maintenance_message"]),
        name=str(row["name"]),
        mark=str(row["mark"]),
        tagline=str(row["tagline"]),
        hero_body=str(row["hero_body"]),
        support_email=str(row["support_email"]),
        color_ink=str(row["color_ink"]),
        color_paper=str(row["color_paper"]),
        color_accent=str(row["color_accent"]),
        color_accent_deep=str(row["color_accent_deep"]),
        color_highlight=str(row["color_highlight"]),
        font=str(row["font"]),
    )
