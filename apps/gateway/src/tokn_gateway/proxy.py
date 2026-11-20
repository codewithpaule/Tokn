"""Reserve, stream, settle.

The order is the design:

1. Validate the request against the catalog.
2. Reserve the worst case — the larger of the primary and fallback prices.
3. Choose a provider: the primary if its breaker allows, otherwise the configured fallback.
4. Stream to the client unbuffered, accumulating usage as events pass.
5. Settle in a finally block, shielded, exactly once, against the entry that served it.

A client that disconnects mid-answer still settles, because the provider generated those
tokens and will bill us for them.

The customer's ``model`` is an alias. It is rewritten to the upstream model id on the way
out, which is also what makes a fallback a fallback rather than a second alias.
"""

from __future__ import annotations

import asyncio
import math
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse

from tokn_core.api_keys import KeyRecord
from tokn_core.auth.ports import Clock
from tokn_core.circuit import CircuitBreaker
from tokn_core.money import Naira
from tokn_core.pricing import PriceSchedule, TokenUsage
from tokn_core.streaming import SseSplitter, UsageAccumulator
from tokn_core.usage import Outcome, UsageEvent
from tokn_gateway.auth import GatewayAuth
from tokn_gateway.providers import ProviderAdapter, UpstreamError

CHARS_PER_TOKEN = 3  # deliberately conservative: over-estimates, never under


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
    def get(self, alias: str) -> CatalogueEntry | None: ...


class WalletStore(Protocol):
    async def reserve(self, *, request_id: str, account_id: str, hold: Naira) -> bool:
        """Atomically hold credit. False means the balance cannot cover it."""

    async def settle(self, *, request_id: str, charge: Naira) -> None:
        """Charge and release the remainder. Idempotent on request_id."""

    async def release(self, *, request_id: str) -> None:
        """Give the whole hold back. Idempotent on request_id."""


class UsageSink(Protocol):
    async def record(self, event: UsageEvent) -> None: ...


def estimate_tokens(text_length: int) -> int:
    return max(1, math.ceil(text_length / CHARS_PER_TOKEN))


class Proxy:
    def __init__(
        self,
        *,
        wallet: WalletStore,
        usage_sink: UsageSink,
        catalogue: Catalogue,
        adapters: dict[str, ProviderAdapter],
        clock: Clock,
        breakers: dict[str, CircuitBreaker] | None = None,
        new_id: Callable[[], str] | None = None,
    ) -> None:
        self._wallet = wallet
        self._usage = usage_sink
        self._catalogue = catalogue
        self._adapters = adapters
        self._clock = clock
        self._breakers = breakers or {}
        self._new_id = new_id or (lambda: uuid.uuid4().hex)

    async def respond(self, key: KeyRecord, payload: dict[str, object]) -> StreamingResponse:
        request_id = self._new_id()
        primary = self._entry(payload)
        max_out = self._max_tokens(payload, primary)
        fallback = self._fallback(primary)
        attemptable = [entry for entry in (primary, fallback) if entry is not None]

        hold = max(
            entry.price.hold(self._estimate_input(payload), max_out) for entry in attemptable
        )
        if not await self._wallet.reserve(
            request_id=request_id, account_id=key.account_id, hold=hold
        ):
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail="your wallet cannot cover this request. Top up to continue.",
            )

        started = self._clock.now()
        served, upstream = await self._open(request_id, key, payload, attemptable, started)
        if upstream is None or served is None:
            raise HTTPException(status_code=502, detail="upstream failed before responding")

        return StreamingResponse(
            self._relay(request_id, key, served, upstream, hold, started),
            media_type="text/event-stream",
            headers={"x-request-id": request_id, "cache-control": "no-store"},
        )

    # -- choosing a provider ----------------------------------------------

    def _usable(self, entry: CatalogueEntry, now: datetime) -> bool:
        breaker = self._breakers.get(entry.provider)
        return True if breaker is None else breaker.allows(now)

    def _record(self, entry: CatalogueEntry, *, ok: bool, now: datetime) -> None:
        breaker = self._breakers.get(entry.provider)
        if breaker is None:
            return
        if ok:
            breaker.record_success()
        else:
            breaker.record_failure(now)

    async def _open(
        self,
        request_id: str,
        key: KeyRecord,
        payload: dict[str, object],
        candidates: list[CatalogueEntry],
        started: datetime,
    ) -> tuple[CatalogueEntry | None, httpx.Response | None]:
        now = self._clock.now()
        attempted_any = False
        last_error: UpstreamError | None = None

        for entry in candidates:
            if not self._usable(entry, now):
                continue
            adapter = self._adapters.get(entry.provider)
            if adapter is None:
                continue
            attempted_any = True
            body = {**payload, "model": entry.upstream_model}
            try:
                return entry, await adapter.open(body)
            except UpstreamError as error:
                last_error = error
                self._record(entry, ok=False, now=now)

        await self._wallet.release(request_id=request_id)
        await self._emit(
            request_id,
            key,
            candidates[0],
            TokenUsage(0, 0),
            Naira.zero(),
            Naira.zero(),
            started,
            Outcome.UPSTREAM_ERROR,
            False,
        )
        if not attempted_any:
            raise HTTPException(status_code=503, detail="no provider is currently available")
        raise HTTPException(
            status_code=502, detail="upstream failed before responding"
        ) from last_error

    def _fallback(self, primary: CatalogueEntry) -> CatalogueEntry | None:
        if not primary.fallback_alias:
            return None
        candidate = self._catalogue.get(primary.fallback_alias)
        if candidate is None or candidate.alias == primary.alias:
            return None
        return candidate

    # -- request validation ------------------------------------------------

    def _entry(self, payload: dict[str, object]) -> CatalogueEntry:
        alias = payload.get("model")
        entry = self._catalogue.get(alias) if isinstance(alias, str) else None
        if entry is None:
            raise HTTPException(status_code=404, detail="unknown model")
        return entry

    def _max_tokens(self, payload: dict[str, object], entry: CatalogueEntry) -> int:
        requested = payload.get("max_tokens") or payload.get("max_completion_tokens") or 1024
        if not isinstance(requested, int) or isinstance(requested, bool) or requested < 1:
            raise HTTPException(status_code=422, detail="max_tokens must be a positive integer")
        if requested > entry.max_output_tokens:
            raise HTTPException(
                status_code=422,
                detail=f"max_tokens exceeds {entry.max_output_tokens} for this model",
            )
        return requested

    def _estimate_input(self, payload: dict[str, object]) -> int:
        size = len(str(payload.get("messages") or "")) + len(str(payload.get("system") or ""))
        return estimate_tokens(size)

    # -- streaming and settlement -----------------------------------------

    async def _relay(
        self,
        request_id: str,
        key: KeyRecord,
        entry: CatalogueEntry,
        upstream: httpx.Response,
        hold: Naira,
        started: datetime,
    ) -> AsyncIterator[bytes]:
        accumulator = UsageAccumulator()
        splitter = SseSplitter()
        streamed = 0
        outcome = Outcome.OK
        try:
            async for chunk in upstream.aiter_raw():
                for line in splitter.feed(chunk):
                    accumulator.feed(entry.protocol, line)
                streamed += len(chunk)
                yield chunk
        except httpx.HTTPError:
            outcome = Outcome.STREAM_ERROR
        except asyncio.CancelledError:
            outcome = Outcome.CLIENT_DISCONNECT
            raise
        except GeneratorExit:
            outcome = Outcome.CLIENT_DISCONNECT
            raise
        finally:
            for line in splitter.flush():
                accumulator.feed(entry.protocol, line)
            await asyncio.shield(
                asyncio.create_task(
                    self._settle(
                        request_id, key, entry, accumulator, streamed, hold, outcome, started
                    )
                )
            )
            self._record(entry, ok=outcome is not Outcome.STREAM_ERROR, now=self._clock.now())
            await upstream.aclose()

    async def _settle(
        self,
        request_id: str,
        key: KeyRecord,
        entry: CatalogueEntry,
        accumulator: UsageAccumulator,
        streamed: int,
        hold: Naira,
        outcome: Outcome,
        started: datetime,
    ) -> None:
        usage = accumulator.usage()
        flagged = False
        if not accumulator.saw_usage:
            if outcome is Outcome.OK:
                outcome = Outcome.USAGE_MISSING
            flagged = True
            usage = TokenUsage(input_tokens=0, output_tokens=estimate_tokens(streamed))

        charge = entry.price.charge(usage)
        if charge.micro > hold.micro:
            charge = hold
            flagged = True

        await self._wallet.settle(request_id=request_id, charge=charge)
        await self._emit(
            request_id,
            key,
            entry,
            usage,
            entry.price.upstream_cost(usage),
            charge,
            started,
            outcome,
            flagged,
        )

    async def _emit(
        self,
        request_id: str,
        key: KeyRecord,
        entry: CatalogueEntry,
        usage: TokenUsage,
        upstream_cost: Naira,
        charged: Naira,
        started: datetime,
        outcome: Outcome,
        flagged: bool,
    ) -> None:
        now = self._clock.now()
        await self._usage.record(
            UsageEvent(
                request_id=request_id,
                account_id=key.account_id,
                key_id=key.id,
                model_alias=entry.alias,
                upstream_model=entry.upstream_model,
                provider=entry.provider,
                usage=usage,
                upstream_cost=upstream_cost,
                charged=charged,
                latency_ms=max(0, int((now - started).total_seconds() * 1000)),
                outcome=outcome,
                flagged_for_review=flagged,
                created_at=now,
            )
        )


def build_router(proxy: Proxy, auth: GatewayAuth) -> APIRouter:
    """The two model endpoints. Bodies are passed through, with the model rewritten."""
    router = APIRouter()

    @router.post("/v1/chat/completions")
    async def chat_completions(
        payload: dict[str, object],
        key: KeyRecord = Depends(auth),  # noqa: B008 - FastAPI idiom
    ) -> StreamingResponse:
        return await proxy.respond(key, payload)

    @router.post("/v1/messages")
    async def messages(
        payload: dict[str, object],
        key: KeyRecord = Depends(auth),  # noqa: B008 - FastAPI idiom
    ) -> StreamingResponse:
        return await proxy.respond(key, payload)

    return router
