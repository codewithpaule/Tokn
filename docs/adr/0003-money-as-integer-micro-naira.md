# 0003. Money is an integer count of micro-naira

- Status: accepted
- Date: 2026-11-20
- Deciders: engineering

## Context

Prices are quoted per million tokens, model outputs are counted in tokens, markups are in
basis points, upstream providers bill in US dollars, and customers pay in naira. Every
one of those steps multiplies or divides. Floating point cannot represent 0.1 exactly, so
rounding drift compounds across a ledger that must reconcile to the last unit.

## Decision

We will store every monetary value as a signed `BIGINT` count of micro-naira, where
1 naira = 1,000,000 micro-naira. Arithmetic is integer arithmetic. Rounding happens once,
at the boundary where a value becomes a charge or a credit, and it always rounds up in
the platform's favour by at most one micro-naira. Prices are `BIGINT` columns in
`model_catalog`, never constants in code. FX conversion from upstream currency records
the rate and timestamp used.

`packages/core/money.py` provides `Naira`, a frozen value type that refuses
non-integer input, including `bool` and `float`.

## Consequences

- The ledger sum equals the stored balance exactly, which is the invariant the nightly
  reconciliation job checks. No epsilon comparisons anywhere.
- Sums of large volumes cannot drift; a million charges of 0.1 micro-naira is exactly
  100,000 micro-naira.
- Humans never see micro-naira. Formatting to two decimals happens at render time only,
  in one function.
- Any new billable unit (cache writes, images, audio) must be expressible as an integer
  count times an integer price, or it needs its own ADR.
