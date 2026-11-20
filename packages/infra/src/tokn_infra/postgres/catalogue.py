"""Reading the catalogue, and writing receipts.

The catalogue is small and changes rarely, so it is cached for a minute. A price change
therefore takes up to a minute to reach the data plane, which is the trade we want: a
database round trip on every request to look up a number that changes monthly is waste.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from tokn_core.catalogue import CatalogueEntry
from tokn_core.money import Naira
from tokn_core.pricing import PriceSchedule
from tokn_core.usage import UsageEvent
from tokn_infra.db.models import ModelCatalog, ModelPrice
from tokn_infra.db.models import UsageEvent as UsageEventRow
from tokn_infra.postgres.session import SessionFactory

CACHE_TTL = timedelta(seconds=60)


@dataclass(slots=True)
class _Cached:
    entry: CatalogueEntry | None
    fetched_at: datetime


class PostgresCatalogue:
    def __init__(self, sessions: SessionFactory) -> None:
        self._sessions = sessions
        self._cache: dict[str, _Cached] = {}

    async def get(self, alias: str) -> CatalogueEntry | None:
        now = datetime.now(tz=UTC)
        cached = self._cache.get(alias)
        if cached is not None and now - cached.fetched_at < CACHE_TTL:
            return cached.entry

        entry = await self._load(alias, now)
        self._cache[alias] = _Cached(entry=entry, fetched_at=now)
        return entry

    async def _load(self, alias: str, now: datetime) -> CatalogueEntry | None:
        async with self._sessions() as session:
            model = await session.scalar(
                select(ModelCatalog).where(
                    ModelCatalog.alias == alias, ModelCatalog.enabled.is_(True)
                )
            )
            if model is None:
                return None
            price = await session.scalar(
                select(ModelPrice)
                .where(ModelPrice.catalog_id == model.id, ModelPrice.effective_from <= now)
                .order_by(ModelPrice.effective_from.desc())
                .limit(1)
            )
            if price is None:
                return None
            return CatalogueEntry(
                alias=model.alias,
                upstream_model=model.upstream_model,
                provider=model.provider,
                protocol=model.protocol,
                price=PriceSchedule(
                    input_per_million=Naira(price.input_per_million_micro),
                    output_per_million=Naira(price.output_per_million_micro),
                    cache_read_per_million=Naira(price.cache_read_per_million_micro),
                    cache_write_per_million=Naira(price.cache_write_per_million_micro),
                    markup_bps=price.markup_bps,
                ),
                max_output_tokens=model.max_output_tokens,
                fallback_alias=model.fallback_alias,
            )


class PostgresUsageSink:
    """One receipt per request. Metadata only — never a prompt, never a completion."""

    def __init__(self, sessions: SessionFactory) -> None:
        self._sessions = sessions

    async def record(self, event: UsageEvent) -> None:
        async with self._sessions() as session, session.begin():
            session.add(
                UsageEventRow(
                    request_id=event.request_id,
                    account_id=_uuid(event.account_id),
                    key_id=_uuid(event.key_id),
                    model_alias=event.model_alias,
                    upstream_model=event.upstream_model,
                    provider=event.provider,
                    input_tokens=event.usage.input_tokens,
                    output_tokens=event.usage.output_tokens,
                    cache_read_tokens=event.usage.cache_read_tokens,
                    cache_write_tokens=event.usage.cache_write_tokens,
                    upstream_cost_micro=event.upstream_cost.micro,
                    charged_micro=event.charged.micro,
                    latency_ms=event.latency_ms,
                    status=event.outcome.value,
                    flagged_for_review=event.flagged_for_review,
                    created_at=event.created_at,
                )
            )


def _uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as error:
        raise ValueError(f"not a UUID: {value}") from error
