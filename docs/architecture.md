# Architecture

Two services, one CLI, one repository. Dependencies point inward.

```
apps/gateway   apps/console        presentation and process wiring
      \           /
       packages/infra             adapters: Postgres, Redis, providers, Paystack, Resend
              |
        packages/core             models, ports, use cases. No framework imports.
```

`packages/core` declares ports as `Protocol`s — `WalletRepository`, `ProviderAdapter`,
`PaymentGateway`, `Clock`, `Mailer`. `packages/infra` implements them. Neither app talks
to PostgreSQL, Redis or an upstream provider directly.

## Services

**Gateway (data plane).** `POST /v1/messages`, `POST /v1/chat/completions`,
`GET /v1/models`, `GET /healthz`, `GET /readyz`. No HTML, no cookies, no admin routes, no
interactive API docs in production. Owns the request lifecycle and nothing else.

**Console (control plane).** Accounts, sessions, API keys, wallet top-ups, usage,
settings, admin. Server-rendered HTML. It never proxies model traffic.

The two services run as separate Fly applications over private networking and connect to
PostgreSQL with different roles: `app_gateway` and `app_console`. A third role,
`app_migrator`, owns schema changes. Ledger tables grant `INSERT` and `SELECT` only, so no
service can rewrite history.

## The life of one request

1. Authenticate: read the key from `Authorization: Bearer` or `x-api-key`, HMAC-SHA256 it
   with the server pepper, look it up in Redis (30 s TTL) then PostgreSQL, compare in
   constant time. Every failure returns the same generic `401`.
2. Authorise: status, expiry, IP allowlist, scopes, spend cap, rate limits (token bucket
   per key and per account) and concurrency cap.
3. Validate: strict Pydantic models, 20 MB body cap, `max_tokens` ceiling per model, alias
   exists and is enabled.
4. Reserve: `UPDATE wallets SET balance_micro = balance_micro - :hold WHERE account_id = :id
   AND balance_micro >= :hold`. Zero rows means `402`. The reservation row is inserted in
   the same transaction.
5. Route: alias to adapter via the catalog, honouring circuit breaker state and an
   explicitly configured fallback.
6. Forward: `httpx` streaming to allowlisted hosts only, no redirects, connect 5 s, read
   120 s, retry only before the first byte.
7. Stream: pass chunks through unbuffered, extract usage from the provider's stream,
   cancel upstream the moment the client disconnects.
8. Settle: inside `asyncio.shield`, in a `finally` block — compute real cost, release the
   unused hold, write one ledger row and one usage event, mark the reservation settled.
   Idempotent on `request_id`.
9. Return `x-request-id` on every response, including errors.

## What we store

Metadata only: model alias, upstream model, token counts, cost, latency, status, and the
account and key that spent it. Never prompt content, never response content, never an
`Authorization` header.

## Money

One prepaid naira wallet per account, shared across every enabled provider. A top-up
credits the internal wallet; it is a customer liability, not revenue. Revenue exists only
as usage settles. Upstream provider accounts are the operator's own and are tracked in a
separate treasury, never as customer balances.

All amounts are integer micro-naira (ADR-0003). The ledger is append-only. Balances are
derived by constraint, not by trust: `CHECK (balance_micro >= 0)` plus a nightly
reconciliation that fails loudly on any difference.

## Failure behaviour

- Client disconnect mid-stream: settlement still runs, exactly once.
- Upstream error before the first byte: hold released, nothing charged, retry or fall back.
- Upstream error after the first byte: charge what was delivered, flag for review.
- Provider balance unknown or depleted: stop routing, fail closed with a retryable error,
  alert the operator before capacity runs out.
- Kill switch: stops new upstream requests while allowing held reservations to settle or
  release.
