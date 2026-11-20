"""What the catalog says about one public model alias.

This is the boundary between the data plane and the database: the gateway asks for an
alias and gets everything it needs to price, validate and route a request — or nothing,
which is a 404.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from tokn_core.pricing import PriceSchedule


@dataclass(frozen=True, slots=True)
class CatalogueEntry:
    alias: str
    upstream_model: str
    provider: str
    protocol: str
    price: PriceSchedule
    max_output_tokens: int
    fallback_alias: str | None = None


class Catalogue(Protocol):
    async def get(self, alias: str) -> CatalogueEntry | None:
        """The enabled entry for this alias, or None."""
