# Tokn

A secure inference gateway. One CLI, one key, one prepaid naira wallet, many models.

Customers point Claude Code, Codex CLI, OpenCode, Cline, Roo or Continue at our base
URL. The gateway authenticates the key, holds credit, forwards the request to an
authorised upstream provider, streams the answer back, charges measured token usage, and
records metadata only. Every request produces a receipt: model, tokens, cost.

- **Verifiable.** Every charge is a ledger line you can audit to the micro-naira.
- **Hard caps.** Per-key spend caps that cannot be exceeded, even under concurrency.
- **Private by default.** No prompts, no responses, no request bodies in logs.
- **Open where it matters.** The code that touches customer traffic ships under
  Apache-2.0.

## Documentation

- [Architecture](docs/architecture.md)
- [Decisions (ADRs)](docs/adr/)
- [Security policy](SECURITY.md)

## Repository layout

```
apps/gateway/     data plane: authenticated, metered, streaming inference
apps/console/     control plane: accounts, keys, billing, admin
packages/core/    domain models, ports and use cases (no framework imports)
packages/infra/   adapters: Postgres, Redis, providers, Paystack, Resend
cli/              Go CLI (open source)
docs/             architecture, ADRs, threat model, runbooks
deploy/           Dockerfiles and fly.toml per app
```

Dependencies point inward: `apps` → `infra` → `core`. `core` depends on nothing.

## Development

Requires [uv](https://docs.astral.sh/uv/) and Python 3.12 (uv installs it for you).

```sh
uv sync --all-packages     # create the workspace environment
uv run pytest              # test
uv run ruff check .        # lint
uv run ruff format --check .
uv run mypy                # strict type check
pre-commit install         # hooks, including gitleaks
```

Configuration is read from the environment. Copy `.env.example` to `.env` and fill in
local values; never commit `.env`.

## Status

Phases 0–4 are code-complete. The persistence layer is now **verified**: migrations
0001–0003 applied to PostgreSQL 18.7 (Neon), and seventeen integration tests run against
it — including one that fires twenty simultaneous reservations at a balance that fits ten
and asserts exactly ten succeed and the balance lands on zero.

What is still unproven: no request has reached a real model provider (adapters are tested
against a mocked transport), no Redis is attached, and the gateway is not yet wired to
PostgreSQL — the console is, and a real signup against Neon writes both the account and
its queued verification email.

```
uv run alembic upgrade head                     # needs TOKN_DATABASE_URL
TOKN_TEST_DATABASE_URL=... uv run pytest        # runs the integration tests too
```
