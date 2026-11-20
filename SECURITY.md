# Security policy

Tokn handles API credentials and prepaid balances. We take reports seriously and we
would rather hear from you early than read about it later.

## Reporting a vulnerability

Email **security@tokn.ng**. Do not open a public issue, and do not test against other
customers' accounts or data.

Please include:

- what you found and why it matters,
- the smallest reproduction you can manage (request, command or screenshot),
- the approximate time you observed it, and the request id from the response header if
  you have one.

We acknowledge reports within 72 hours. We aim to give you a first assessment within
five working days and to ship a fix for anything critical within 30 days.

Never send real API keys, real prompts, customer data or full request bodies. If you
believe a secret is exposed, send the prefix only and we will rotate it.

## Scope

In scope:

- `tokn.ng` and subdomains we operate,
- the gateway API (`/v1/*`),
- the console,
- the open-source CLI and gateway source.

Out of scope: denial-of-service via volumetric traffic, findings that require a
compromised device or a rooted phone, and reports generated purely by automated scanners
without a demonstrated impact.

## Safe harbour

We will not pursue legal action against researchers who act in good faith under this
policy, stay within scope, avoid privacy violations and data destruction, and give us
reasonable time to fix before disclosure. We will credit you in the release notes if you
would like, or keep you anonymous if you would prefer.

## What we already assume

- No prompts or responses are stored by default.
- API keys are stored only as HMAC-SHA256 hashes.
- Money is integer micro-naira in an append-only ledger.
- Upstream provider credentials never leave the gateway.
