from __future__ import annotations

from datetime import UTC, datetime
from fractions import Fraction

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tokn_core.errors import InvalidAmountError
from tokn_core.money import Naira
from tokn_core.pricing import BPS, MILLION, FxRate, PriceSchedule, TokenUsage, ceil_div

tokens = st.integers(0, 2_000_000)
prices = st.integers(0, 10**9)
markups = st.integers(0, 2000)


def schedule(
    input_price: int = 500_000,
    output_price: int = 2_000_000,
    markup_bps: int = 250,
) -> PriceSchedule:
    return PriceSchedule(
        input_per_million=Naira(input_price),
        output_per_million=Naira(output_price),
        cache_read_per_million=Naira(input_price // 10),
        cache_write_per_million=Naira(input_price),
        markup_bps=markup_bps,
    )


def exact_charge(usage: TokenUsage, price: PriceSchedule) -> Fraction:
    weighted = (
        usage.input_tokens * price.input_per_million.micro
        + usage.output_tokens * price.output_per_million.micro
        + usage.cache_read_tokens * price.cache_read_per_million.micro
        + usage.cache_write_tokens * price.cache_write_per_million.micro
    )
    return Fraction(weighted * (BPS + price.markup_bps), MILLION * BPS)


def ceil_of(value: Fraction) -> int:
    return -((-value.numerator) // value.denominator)


@given(st.integers(0, 10**15), st.integers(1, 10**6))
def test_ceil_div_is_the_smallest_sufficient_integer(numerator: int, denominator: int) -> None:
    result = ceil_div(numerator, denominator)
    assert result * denominator >= numerator
    assert (result - 1) * denominator < numerator


def test_ceil_div_rejects_zero_denominator() -> None:
    with pytest.raises(InvalidAmountError, match="denominator"):
        ceil_div(10, 0)


@given(tokens, tokens, tokens, tokens, prices, prices, markups)
def test_charge_is_the_exact_value_rounded_up_once(
    input_tokens: int,
    output_tokens: int,
    cache_read: int,
    cache_write: int,
    input_price: int,
    output_price: int,
    markup_bps: int,
) -> None:
    usage = TokenUsage(input_tokens, output_tokens, cache_read, cache_write)
    price = schedule(input_price, output_price, markup_bps)
    charged = price.charge(usage).micro
    expected = exact_charge(usage, price)
    assert charged >= expected, "never undercharge"
    assert Fraction(charged - 1) < expected, "never overcharge by more than one micro-naira"
    assert charged == ceil_of(expected)


def test_zero_usage_costs_nothing() -> None:
    assert schedule().charge(TokenUsage(0, 0)).is_zero()
    assert schedule().upstream_cost(TokenUsage(0, 0)).is_zero()


@given(tokens, tokens, prices, prices, markups)
def test_markup_never_lowers_the_price(
    input_tokens: int, output_tokens: int, input_price: int, output_price: int, markup_bps: int
) -> None:
    usage = TokenUsage(input_tokens, output_tokens)
    price = schedule(input_price, output_price, markup_bps)
    assert price.charge(usage).micro >= price.upstream_cost(usage).micro


@given(tokens, tokens, prices, prices, st.integers(0, 500), st.integers(0, 500))
def test_markup_is_monotonic(
    input_tokens: int,
    output_tokens: int,
    input_price: int,
    output_price: int,
    markup_a: int,
    markup_b: int,
) -> None:
    lower, higher = min(markup_a, markup_b), max(markup_a, markup_b)
    usage = TokenUsage(input_tokens, output_tokens)
    cheap = schedule(input_price, output_price, lower)
    dear = schedule(input_price, output_price, higher)
    assert dear.charge(usage).micro >= cheap.charge(usage).micro


@given(tokens, tokens, tokens, prices, prices, markups)
def test_hold_covers_any_usage_within_its_bounds(
    input_tokens: int,
    output_tokens: int,
    slack: int,
    input_price: int,
    output_price: int,
    markup_bps: int,
) -> None:
    price = schedule(input_price, output_price, markup_bps)
    hold = price.hold(input_tokens + slack, output_tokens + slack)
    actual = price.charge(TokenUsage(input_tokens, output_tokens))
    assert actual.micro <= hold.micro


@given(tokens, tokens, prices, prices, markups)
def test_cache_reads_never_cost_more_than_fresh_input(
    cache_read: int, input_tokens: int, input_price: int, output_price: int, markup_bps: int
) -> None:
    price = schedule(input_price, output_price, markup_bps)
    cached = price.charge(TokenUsage(0, 0, cache_read, 0))
    fresh = price.charge(TokenUsage(cache_read, 0))
    assert cached.micro <= fresh.micro


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"input_tokens": -1, "output_tokens": 0}, "input_tokens"),
        ({"input_tokens": 0, "output_tokens": -1}, "output_tokens"),
        ({"input_tokens": True, "output_tokens": 0}, "input_tokens"),
    ],
)
def test_usage_rejects_impossible_counts(kwargs: dict[str, int], match: str) -> None:
    with pytest.raises(InvalidAmountError, match=match):
        TokenUsage(**kwargs)


def test_negative_prices_are_rejected() -> None:
    with pytest.raises(InvalidAmountError, match="input_per_million"):
        PriceSchedule(input_per_million=Naira(-1), output_per_million=Naira(0))


def test_negative_markup_is_rejected() -> None:
    with pytest.raises(InvalidAmountError, match="markup_bps"):
        PriceSchedule(input_per_million=Naira(1), output_per_million=Naira(1), markup_bps=-1)


def fx_rate(micro_naira_per_unit: int = 1_600_000_000) -> FxRate:
    return FxRate(
        from_currency="USD",
        to_currency="NGN",
        micro_naira_per_unit=micro_naira_per_unit,
        recorded_at=datetime(2026, 11, 20, tzinfo=UTC),
    )


@given(st.integers(0, 10**12), st.integers(1, 10**12))
def test_fx_rounds_up_and_never_loses_a_micro_naira(amount: int, rate: int) -> None:
    converted = fx_rate(rate).to_naira(amount).micro
    expected = Fraction(amount * rate, MILLION)
    assert converted >= expected
    assert Fraction(converted - 1) < expected


def test_fx_conversion_is_exact_at_a_round_rate() -> None:
    one_dollar = 1_000_000  # micro-dollars
    assert fx_rate().to_naira(one_dollar) == Naira(1_600_000_000)


def test_fx_requires_both_currencies() -> None:
    with pytest.raises(InvalidAmountError, match="currency"):
        FxRate(
            from_currency="",
            to_currency="NGN",
            micro_naira_per_unit=1,
            recorded_at=datetime(2026, 11, 20, tzinfo=UTC),
        )


@given(st.integers(0, 10**9))
def test_fx_is_monotonic(amount: int) -> None:
    rate = fx_rate()
    assert rate.to_naira(amount + 1_000_000).micro >= rate.to_naira(amount).micro
