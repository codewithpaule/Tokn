# 0005. Authentication: server-side sessions, double-submit CSRF, no enumeration

- Status: accepted
- Date: 2026-11-20
- Deciders: engineering

## Context

The console holds a prepaid naira balance and the keys that spend it. Authentication is
therefore the highest-value target in the product, and nearly everything an attacker can
learn from a login form — whether an address is registered, whether a password was close,
whether a link was already used — is a lever they can pull.

We also cannot afford a dependency that owns our session model: the requirements name
specific lifetimes, rotation points and cookie attributes.

## Decision

**Passwords.** Argon2id at OWASP parameters (19 MiB, t=2, p=1), with `needs_rehash` so
parameters can rise without a flag day. Policy is length first (12 minimum, 256 maximum),
then a bundled list of common passwords, then the account's own email address. The hash
never leaves the `PasswordHasher` port.

**Sessions are server-side.** The browser gets 256 bits of randomness in
`__Host-session`; PostgreSQL stores only HMAC-SHA256(id, pepper). Idle timeout 30 minutes,
absolute timeout 7 days, checked on every request. A new id is issued on every login, so a
session fixed before login is useless after it.

**CSRF is double-submit with a `__Host-csrf` cookie.** No token is ever accepted from a
header alone, and `__Host-` prevents a subdomain from setting the cookie. Every non-GET
route depends on `require_csrf`, which compares cookie and form field in constant time.

**Nothing reveals whether an account exists.** Signup, password reset and login return one
message and one status for both outcomes. When the account is missing, login still performs
an Argon2 verification against a dummy hash so the response time matches.

**Tokens are single use.** Verification and reset links are 256-bit random values with a
keyed digest in the database, 30 minutes of life, and a consume operation that marks them
used atomically. Recovery codes follow the same rule: ten codes, stored hashed, each
burned on use.

**Throttling is per account and per address**, doubling from one second to fifteen
minutes, resetting after an hour without a failure.

## Consequences

- A database leak yields no usable session, token, recovery code or password.
- We own the session lifecycle, so the lifetimes in the requirements are the lifetimes in
  the code, and they are covered by tests that advance a frozen clock.
- The console depends on an `AuthStore` port. Only the in-memory adapter exists today,
  which is enough for the security tests and local development but is not durable. The
  PostgreSQL adapter is the first task of Phase 3, and until it lands the console refuses
  to start outside `development`.
- CSRF via double-submit assumes `__Host-` cookies and a single origin. If we ever serve
  the console from more than one hostname, this needs revisiting rather than widening.
- Login failures are cheap to attempt once per second, by design. The concurrency cap and
  edge rate limiting handle volume; the backoff handles guessing.
