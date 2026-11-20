# 0002. One ORM: no Django alongside SQLAlchemy

- Status: accepted
- Date: 2026-11-20
- Deciders: engineering

## Context

A request during Phase 0 asked for Django in addition to FastAPI. Django is genuinely
good at the control plane: sessions, CSRF, authentication, an admin and migrations all
come for free. But Tokn's most dangerous code is not the web tier — it is the wallet and
ledger, where a single divergent mapping of `balance_micro` produces silent financial
loss.

Running Django beside SQLAlchemy would mean two ORMs and two migration histories over the
same PostgreSQL database. The wallet, ledger, reservation and usage tables would have to
be owned by exactly one side while the other reads them, and every schema change would
need two reviewers to notice a drifted model. Every future contributor becomes a possible
source of that bug.

## Decision

We will use FastAPI with SQLAlchemy 2.0 async and Alembic for both services. We will not
add Django, and we will not add a second ORM or a second migration tool for any table
that holds money.

## Consequences

- One definition of the ledger, enforced by database constraints and property-based
  tests, in one place.
- Session, CSRF, 2FA, password reset and admin are implemented by us against the
  security requirements. This is more code, but it is code the phase gates explicitly
  test for, and it carries no framework upgrade risk.
- The console's ergonomics come from Jinja2 partials plus htmx rather than Django
  templates plus forms. Templates stay small because every component is built once and
  reused.
- If a future need strongly favours a batteries-included admin, the answer is a separate
  service in a separate repository with a read-only view, never a second writer of money.
