# 0004. Reserve the worst case, settle exactly once

- Status: accepted
- Date: 2026-11-20
- Deciders: engineering

## Context

A streaming request creates a window between "we agreed to serve this" and "we know what
it cost". During that window the customer may run out of credit, disconnect, retry, or
the upstream may fail after sending half an answer. Charging after the fact means a
customer can spend money they do not have; charging before the fact means guessing.

Upstream usage reporting is also unreliable at the edges: a stream can end without a
final usage event, and providers count tokens differently.

## Decision

Before a request leaves our process we reserve the worst case: estimated input tokens at
the input price plus the caller's `max_tokens` at the output price, with markup, rounded
up. The reservation is taken with

```sql
UPDATE wallets SET balance_micro = balance_micro - :hold
 WHERE account_id = :id AND balance_micro >= :hold
```

Zero rows updated means HTTP 402 and no upstream call. The reservation row is inserted in
the same transaction.

After the response finishes we settle in a `finally` block inside `asyncio.shield`:
compute the real charge from measured usage, release the unused hold, write one ledger
row and one usage event, and mark the reservation settled. Settlement is idempotent on
`request_id` — a retry, a duplicate callback or a second settle attempt is a no-op.

`Charge <= hold` is enforced twice: in the domain state machine and by a database CHECK
constraint. A settlement that would exceed the hold is a bug, not a customer problem, so
it fails loudly.

If the upstream reports no usage we charge for the tokens actually streamed and set
`flagged_for_review`. We never guess silently and never undercharge to zero.

## Consequences

- A customer can never spend more than their balance, even with many concurrent streams,
  because the balance is reduced before the upstream call and the conditional UPDATE is
  atomic.
- Money is held slightly longer than strictly necessary: the difference between the
  worst case and the real cost is unavailable until settlement, which is milliseconds
  after the stream ends.
- A crashed process leaves a held reservation. A sweeper releases reservations older
  than 15 minutes, so the failure mode is a temporary over-hold, never a free request.
- `request_id` is the idempotency key end to end: in the reservation, in the usage event,
  and in the ledger reference.
- The ledger and the usage event are written in one transaction, so a receipt can never
  exist without its charge, or a charge without its receipt.
