"""One wallet, many providers.

A customer tops up once and spends that single balance on DeepSeek, OpenAI and Anthropic
models. Model choice changes the route and the price schedule, never the wallet. A top-up
is a liability, not revenue: revenue exists only where usage was delivered.
"""

from __future__ import annotations

from datetime import UTC, datetime

from tokn_core.ledger import EntryKind, Ledger
from tokn_core.money import Naira
from tokn_core.pricing import FxRate, PriceSchedule, TokenUsage
from tokn_core.wallet import Wallet

# Prices in micro-naira per million tokens: 240_000_000 is 240 naira per million tokens.
SCHEDULES: dict[str, tuple[str, int, int]] = {
    "deepseek-chat": ("deepseek", 180_000_000, 720_000_000),
    "gpt-4o-mini": ("openai", 240_000_000, 960_000_000),
    "claude-sonnet": ("anthropic", 4_800_000_000, 24_000_000_000),
}

USAGE = TokenUsage(input_tokens=1_500, output_tokens=600)


def schedule_for(alias: str) -> PriceSchedule:
    _, input_price, output_price = SCHEDULES[alias]
    return PriceSchedule(
        input_per_million=Naira(input_price),
        output_per_million=Naira(output_price),
        markup_bps=400,
    )


def test_one_wallet_pays_for_three_different_providers() -> None:
    opening = Naira.from_naira("20000")
    wallet = Wallet(opening)
    ledger = Ledger()
    ledger.append(EntryKind.TOPUP, opening, "paystack-reference-1")

    charged = 0
    for index, alias in enumerate(SCHEDULES):
        price = schedule_for(alias)
        hold = price.hold(estimated_input_tokens=2_000, max_output_tokens=1_000)
        assert hold.micro >= price.charge(USAGE).micro, "the hold must cover real usage"

        wallet.hold(hold)
        charge = price.charge(USAGE)
        wallet.settle(hold, charge)
        ledger.append(EntryKind.USAGE, -charge, f"usage-{alias}-{index}")

        charged += charge.micro
        assert wallet.balance.micro == opening.micro - charged
        assert wallet.balance.micro >= 0

    assert ledger.is_consistent()
    assert ledger.balance.micro == opening.micro - charged
    assert charged > 0


def test_switching_provider_does_not_change_the_wallet() -> None:
    wallet = Wallet(Naira.from_naira("500"))
    before = wallet.balance

    for alias in SCHEDULES:
        price = schedule_for(alias)
        hold = price.hold(2_000, 1_000)
        wallet.hold(hold)
        wallet.settle(hold, price.charge(USAGE))
        assert not wallet.balance.is_negative()

    assert wallet.balance.micro < before.micro, "the same wallet paid for all three"


def test_topups_are_liabilities_and_usage_is_revenue() -> None:
    ledger = Ledger()
    ledger.append(EntryKind.TOPUP, Naira.from_naira("1000"), "topup-1")
    ledger.append(EntryKind.TOPUP, Naira.from_naira("2000"), "topup-2")
    ledger.append(EntryKind.USAGE, -Naira.from_naira("150"), "usage-1")
    ledger.append(EntryKind.USAGE, -Naira.from_naira("50"), "usage-2")

    topups = sum(e.amount.micro for e in ledger.entries if e.kind is EntryKind.TOPUP)
    revenue = -sum(e.amount.micro for e in ledger.entries if e.kind is EntryKind.USAGE)

    assert topups == Naira.from_naira("3000").micro
    assert revenue == Naira.from_naira("200").micro
    assert ledger.balance.micro == topups - revenue, "balance is unspent liability"
    assert revenue < topups, "a top-up is not income until usage is delivered"


def test_a_foreign_currency_cost_is_converted_before_margin_is_judged() -> None:
    """Upstream cost arrives in the provider's currency; margin only means anything in naira."""
    rate = FxRate(
        from_currency="USD",
        to_currency="NGN",
        micro_naira_per_unit=1_600_000_000,  # 1,600 naira per dollar
        recorded_at=datetime(2026, 11, 20, tzinfo=UTC),
    )
    # 1,500 input at $0.15/M plus 600 output at $0.60/M, in micro-dollars.
    cost_micro_usd = 585
    cost_naira = rate.to_naira(cost_micro_usd)
    retail = schedule_for("gpt-4o-mini").charge(USAGE)

    assert cost_naira == Naira(936_000)
    assert retail.micro > cost_naira.micro, "retail must clear the converted upstream cost"
