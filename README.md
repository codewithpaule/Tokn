# Brewkeg

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

## Console and public site

`apps/console` serves three things:

| Path | What it is |
| --- | --- |
| `/login`, `/signup`, `/forgot-password`, `/reset-password` | Themed auth with Argon2id, server-side sessions, CSRF and login backoff |
| `/dashboard/...` | Customer console: overview, setup, requests, keys, billing, settings, referrals, team, feedback |
| `/api/...` | JSON for the console and for clients: auth, keys, wallet, requests, feedback |
| `/admin` | Operator screen for availability, copy, colours and font, guarded by `accounts.is_admin` |
| everything else | The `brewkeg.dev` design folder, with clean URLs, trackers removed, and `/theme.css` applied |

`/theme.css` is rendered from the `site_settings` row, so a colour or font saved in
`/admin` reaches the marketing pages and the console. In development every account is an
admin; in production set `TOKN_ADMIN_EMAILS` to the addresses that may open `/admin`.

Sign up auto-verifies the email because this build has no mail worker. The account starts
with an empty wallet and no admin rights outside development, and the welcome message is
queued in `email_outbox`, not sent inline.

## Documentation

- [Architecture](docs/architecture.md)
- [Decisions (ADRs)](docs/adr/)
- [Security policy](SECURITY.md)

## Repository layout

```
apps/gateway/     data plane: authenticated, metered, streaming inference
apps/console/     control plane: auth, dashboard, admin, public site host
packages/core/    domain models, ports and use cases (no framework imports)
packages/infra/   adapters: Postgres, Redis, providers, Paystack, Resend
docs/             architecture, ADRs, threat model, runbooks
deploy/           Dockerfiles and fly.toml per app
```

Dependencies point inward: `apps` -> `infra` -> `core`. `core` depends on nothing.

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

Run the console with the design folder in place:

```sh
uv run uvicorn tokn_console:create_app --factory --host 127.0.0.1 --port 8074
```

## Status

Phases 0-4 are code-complete. Migrations 0001-0008 cover money, auth, keys, email,
site settings, the admin flag and feedback. The console now has real sign up, sign in,
password reset, sessions, API key creation and revocation, wallet and receipt reads,
feedback, and the operator's theme screen.

What is still unproven: no request has reached a real model provider (adapters are tested
against a mocked transport), no Redis is attached, and card payments are not connected.
The billing screen says so instead of showing a checkout that would fail.

```
uv run alembic upgrade head                     # needs TOKN_DATABASE_URL
TOKN_TEST_DATABASE_URL=... uv run pytest        # runs the integration tests too
```
