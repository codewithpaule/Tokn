# 0006. Streaming: reserve, relay, settle — and what to do when usage goes missing

- Status: accepted
- Date: 2026-11-20
- Deciders: engineering

## Context

A streaming answer is the one place where money, latency and failure all meet. The client
can disconnect at any byte. The upstream can fail before the first token or in the middle
of a sentence. Usage arrives in a different shape for each protocol, and sometimes it does
not arrive at all.

We cannot hold a database transaction open for the length of a model response, and we
cannot wait until the end to discover the customer could not afford it.

## Decision

**Reserve before, settle after.** The hold is the worst case: estimated input tokens at
the input price plus the caller's `max_tokens` at the output price, with markup. If the
wallet cannot cover it the request never reaches upstream. Settlement runs in a `finally`,
inside `asyncio.shield`, exactly once, on `request_id` (ADR-0004).

**Parse lines, not chunks.** A network chunk is not an SSE event: it can carry half an
event or three of them. `SseSplitter` buffers and emits complete lines, and a test feeds
the same payload cut at every possible byte to prove usage is never lost. This was a real
bug caught during the phase, not a hypothetical.

**A hold is a hard ceiling.** If the measured charge exceeds the reserved hold, we charge
the hold and flag the event for review. That should be impossible — the hold covers the
worst case — so when it happens it is a pricing or estimation bug, and the response
should be a person looking at it, not a silent overcharge.

**Missing usage is charged conservatively and flagged.** When a stream ends without a
usage event we estimate output tokens from the bytes actually relayed, at roughly three
characters per token, rounded up, and set `flagged_for_review`. We do not give the answer
away, and we do not invent a precise number we cannot defend.

**Disconnects settle.** `GeneratorExit` and `CancelledError` both mark the outcome
`client_disconnect` and still settle, because the provider generated those tokens and will
bill us for them.

## Consequences

- The gateway can run out of "reserved" money but never out of real money.
- A failed upstream costs the customer nothing and costs us a request.
- The estimate in `estimate_tokens` (three characters per token) is deliberately
  pessimistic: it over-reserves slightly and over-charges only in the flagged path.
- The relay adds one pass over each chunk to split lines and parse `data:` payloads. That
  is microseconds against a model response, but it is not free, and the Phase 8 load test
  is where we will find out what it costs at 200 concurrent streams.
- Events flagged for review need a human path, which is Phase 5's reconciliation job.
