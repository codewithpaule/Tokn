"""The reserve/stream/settle lifecycle, over HTTP where possible and directly where not."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from tokn_core.api_keys import KeyRecord
from tokn_core.money import Naira
from tokn_core.pricing import PriceSchedule, TokenUsage
from tokn_core.usage import Outcome
from tokn_gateway.providers import UpstreamError
from tokn_gateway.proxy import CatalogueEntry, Proxy, build_router

BASE = "https://gateway.test"
NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
KEY = KeyRecord(
    id="key_1",
    account_id="acc_1",
    prefix="tokn_live_abcdef",
    digest="x",
    scopes=frozenset({"infer"}),
)
PRICE = PriceSchedule(
    input_per_million=Naira(1_000_000), output_per_million=Naira(2_000_000), markup_bps=0
)
ENTRY = CatalogueEntry(
    alias="test-model",
    upstream_model="upstream-1",
    provider="fake",
    protocol="openai",
    price=PRICE,
    max_output_tokens=4096,
)

OPENAI_CHUNKS = [
    b'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n',
    b'data: {"choices":[{"delta":{"content":" there"}}]}\n\n',
    b'data: {"choices":[],"usage":{"prompt_tokens":100,"completion_tokens":50}}\n\n',
    b"data: [DONE]\n\n",
]


class FrozenClock:
    def now(self) -> datetime:
        return NOW


class FakeWallet:
    def __init__(self, opening_micro: int) -> None:
        self.balance = Naira(opening_micro)
        self.holds: dict[str, Naira] = {}
        self.reserved: list[tuple[str, Naira]] = []
        self.settled: list[tuple[str, Naira]] = []
        self.released: list[str] = []

    async def reserve(self, *, request_id: str, account_id: str, hold: Naira) -> bool:
        if hold.micro > self.balance.micro:
            return False
        self.balance = self.balance - hold
        self.holds[request_id] = hold
        self.reserved.append((request_id, hold))
        return True

    async def settle(self, *, request_id: str, charge: Naira) -> None:
        if request_id in {rid for rid, _ in self.settled}:
            return
        hold = self.holds.pop(request_id, Naira.zero())
        self.balance = self.balance + (hold - charge)
        self.settled.append((request_id, charge))

    async def release(self, *, request_id: str) -> None:
        if request_id in {rid for rid, _ in self.settled}:
            return
        hold = self.holds.pop(request_id, Naira.zero())
        self.balance = self.balance + hold
        self.released.append(request_id)


class FakeSink:
    def __init__(self) -> None:
        self.events = []

    async def record(self, event) -> None:
        self.events.append(event)


class FakeCatalogue:
    def __init__(self, entries: list[CatalogueEntry]) -> None:
        self._entries = {entry.alias: entry for entry in entries}

    def get(self, alias: str) -> CatalogueEntry | None:
        return self._entries.get(alias)


class ChunkStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def __aiter__(self):
        for chunk in self._chunks:
            yield chunk


class FakeAdapter:
    def __init__(
        self, chunks: list[bytes] | None = None, error: UpstreamError | None = None
    ) -> None:
        self.chunks = chunks or OPENAI_CHUNKS
        self.error = error
        self.payloads: list[dict[str, object]] = []

    async def open(self, payload: dict[str, object]) -> httpx.Response:
        self.payloads.append(payload)
        if self.error is not None:
            raise self.error
        return httpx.Response(200, stream=ChunkStream(self.chunks))


def build(
    *,
    opening_micro: int = 10_000_000,
    chunks: list[bytes] | None = None,
    error: UpstreamError | None = None,
    entries: list[CatalogueEntry] | None = None,
) -> tuple[FastAPI, Proxy, FakeWallet, FakeSink, FakeAdapter]:
    wallet = FakeWallet(opening_micro)
    sink = FakeSink()
    adapter = FakeAdapter(chunks, error)
    proxy = Proxy(
        wallet=wallet,
        usage_sink=sink,
        catalogue=FakeCatalogue(entries or [ENTRY]),
        adapters={"fake": adapter},
        clock=FrozenClock(),
        new_id=lambda: "req_1",
    )

    async def fake_auth() -> KeyRecord:
        return KEY

    app = FastAPI(docs_url=None, openapi_url=None)
    app.include_router(build_router(proxy, fake_auth))
    return app, proxy, wallet, sink, adapter


async def call(app: FastAPI, payload: dict[str, object], path: str = "/v1/chat/completions"):
    async with AsyncClient(transport=ASGITransport(app=app), base_url=BASE) as client:
        return await client.post(path, json=payload)


async def test_a_request_is_streamed_and_charged_for_real_usage() -> None:
    app, _, wallet, sink, adapter = build()
    payload = {
        "model": "test-model",
        "messages": [{"role": "user", "content": "x" * 300}],
        "max_tokens": 64,
    }

    response = await call(app, payload)

    assert response.status_code == 200
    assert b"Hello" in response.content
    assert response.headers["x-request-id"] == "req_1"

    usage = TokenUsage(input_tokens=100, output_tokens=50)
    expected = PRICE.charge(usage)
    assert wallet.settled == [("req_1", expected)]
    assert wallet.balance.micro == 10_000_000 - expected.micro
    assert sink.events[0].outcome is Outcome.OK
    assert sink.events[0].usage == usage
    assert sink.events[0].flagged_for_review is False
    assert adapter.payloads[0]["model"] == "test-model"


async def test_the_hold_covers_the_worst_case_before_upstream_is_called() -> None:
    app, _, wallet, _, adapter = build()
    await call(app, {"model": "test-model", "messages": [], "max_tokens": 4096})

    assert adapter.payloads, "upstream was called"
    _request_id, hold = wallet.reserved[0]
    charged = wallet.settled[0][1]
    assert hold.micro >= charged.micro, "the hold must cover the real charge"
    assert wallet.balance.micro == 10_000_000 - charged.micro


async def test_insufficient_funds_stops_before_upstream() -> None:
    app, _, wallet, sink, adapter = build(opening_micro=0)

    response = await call(app, {"model": "test-model", "messages": [], "max_tokens": 64})

    assert response.status_code == 402
    assert "Top up" in response.json()["detail"]
    assert adapter.payloads == []
    assert sink.events == []
    assert wallet.balance.micro == 0


async def test_an_upstream_failure_releases_the_hold_and_charges_nothing() -> None:
    app, _, wallet, sink, _ = build(error=UpstreamError(500, "boom"))

    response = await call(app, {"model": "test-model", "messages": [], "max_tokens": 64})

    assert response.status_code == 502
    assert wallet.settled == []
    assert wallet.balance.micro == 10_000_000, "the hold came back untouched"
    assert sink.events[0].outcome is Outcome.UPSTREAM_ERROR
    assert sink.events[0].charged.is_zero()


async def test_missing_usage_is_flagged_and_charged_conservatively() -> None:
    chunks = [b'data: {"choices":[{"delta":{"content":"x" * 300}}]}\n\n', b"data: [DONE]\n\n"]
    app, _, wallet, sink, _ = build(chunks=chunks)

    response = await call(app, {"model": "test-model", "messages": [], "max_tokens": 64})

    assert response.status_code == 200
    event = sink.events[0]
    assert event.outcome is Outcome.USAGE_MISSING
    assert event.flagged_for_review is True
    assert event.charged.micro > 0, "we do not silently give the answer away"
    assert wallet.settled[0][1] == event.charged


async def test_an_unknown_model_is_a_404_without_a_hold() -> None:
    app, _, wallet, _, _ = build()
    response = await call(app, {"model": "does-not-exist", "messages": []})

    assert response.status_code == 404
    assert wallet.balance.micro == 10_000_000


async def test_max_tokens_above_the_model_limit_is_refused() -> None:
    app, _, wallet, _, adapter = build()
    response = await call(app, {"model": "test-model", "messages": [], "max_tokens": 5000})

    assert response.status_code == 422
    assert "exceeds" in response.json()["detail"]
    assert adapter.payloads == []
    assert wallet.balance.micro == 10_000_000


async def test_a_client_disconnect_still_settles_exactly_once() -> None:
    _, proxy, wallet, sink, adapter = build()
    upstream = await adapter.open({"model": "test-model"})

    stream = proxy._relay("req_1", KEY, ENTRY, upstream, Naira(1_000_000), NOW)
    first = await stream.__anext__()
    assert b"Hello" in first
    await stream.aclose()  # what Starlette does when the client goes away

    assert len(wallet.settled) == 1
    assert sink.events[0].outcome is Outcome.CLIENT_DISCONNECT


async def test_settlement_is_idempotent_on_the_request_id() -> None:
    _, proxy, wallet, sink, adapter = build()
    upstream = await adapter.open({"model": "test-model"})

    stream = proxy._relay("req_1", KEY, ENTRY, upstream, Naira(1_000_000), NOW)
    async for _ in stream:
        pass
    await stream.aclose()

    assert len(wallet.settled) == 1
    assert len(sink.events) == 1


async def test_the_charge_never_exceeds_the_hold() -> None:
    generous = [b'data: {"usage":{"prompt_tokens":1000000,"completion_tokens":1000000}}\n\n']
    _, proxy, wallet, sink, adapter = build(chunks=generous)
    upstream = await adapter.open({"model": "test-model"})
    hold = Naira(500)

    stream = proxy._relay("req_1", KEY, ENTRY, upstream, hold, NOW)
    async for _ in stream:
        pass
    await stream.aclose()

    assert wallet.settled[0][1].micro <= hold.micro
    assert sink.events[0].flagged_for_review is True
