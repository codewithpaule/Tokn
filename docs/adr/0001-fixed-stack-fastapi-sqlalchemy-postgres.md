# 0001. Adopt the fixed stack: FastAPI, SQLAlchemy async, PostgreSQL, Jinja2

- Status: accepted
- Date: 2026-11-20
- Deciders: engineering

## Context

Tokn has two very different workloads. The data plane is a hot streaming proxy that must
add under 50 ms p95 of its own latency across 200 concurrent streams and must never leak
a reservation. The control plane is a browser-facing application with sessions, CSRF,
two-factor authentication, payments and an admin surface.

Those workloads have different scaling, security and failure characteristics, so they run
as two services with separate database roles. Both are Python. We want one language, one
test toolchain and one dependency policy, and we want the money logic to be reusable by
the open-source gateway.

## Decision

We will build both services in Python 3.12 with FastAPI, Pydantic v2, SQLAlchemy 2.0
async, Alembic and asyncpg, managed as a `uv` workspace. PostgreSQL holds everything,
including money and sessions. Redis holds only data we can afford to lose. The console is
server-rendered Jinja2 with vendored htmx and the Alpine CSP build; there is no Node
toolchain and no runtime CDN.

## Consequences

- One ORM and one migration history over the money tables. Ledger invariants are defined
  once, in `packages/core`, and tested once.
- `core` stays importable by the open-source repository because it has no framework
  dependencies; adapters are injected at the edge.
- We hand-build what a batteries-included framework would give us: sessions, CSRF,
  password reset and admin. Each is written against the requirements in the security
  section and covered by tests, which is the point — we need to prove those behaviours.
- The console renders HTML on the server, so there is no client build step, no hydration
  cost and no JavaScript supply chain beyond two vendored, hash-pinned files.
- Runtime is plain `uvicorn`, not `uvicorn[standard]`. The extra pulls `websockets`,
  `watchfiles` and `httptools`; the gateway speaks HTTP only, and added latency in this
  service is dominated by upstream model time, not by request parsing. If the Phase 8
  load test shows event-loop cost mattering, we add `uvloop` on its own with the
  benchmark that justified it.
- Revisit if the console's UI complexity outgrows htmx. That would be a new ADR, not a
  silent rewrite.
