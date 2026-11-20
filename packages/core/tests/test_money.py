from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tokn_core.money import MICRO_PER_NAIRA, Naira

# Hypothesis bound: one trillion naira either way, far beyond any real balance.
LOW = -(10**12)
HIGH = 10**12


def test_zero_is_zero() -> None:
    assert Naira.zero().micro == 0
    assert Naira.zero().is_zero()


def test_from_naira_is_exact() -> None:
    assert Naira.from_naira(Decimal("1")).micro == MICRO_PER_NAIRA
    assert Naira.from_naira("0.000001").micro == 1
    assert Naira.from_naira(1500).micro == 1500 * MICRO_PER_NAIRA


def test_from_naira_rounds_half_up_to_micro() -> None:
    assert Naira.from_naira(Decimal("0.0000005")).micro == 1
    assert Naira.from_naira(Decimal("0.0000004")).micro == 0


@pytest.mark.parametrize("bad", [1.5, True, "12"])
def test_rejects_non_integer_micro(bad: object) -> None:
    with pytest.raises(TypeError, match="micro"):
        Naira(bad)  # type: ignore[arg-type]


def test_rejects_non_finite() -> None:
    with pytest.raises(ValueError, match="finite"):
        Naira.from_naira(Decimal("NaN"))


def test_arithmetic() -> None:
    assert Naira(300) + Naira(500) == Naira(800)
    assert Naira(300) - Naira(500) == Naira(-200)
    assert -Naira(300) == Naira(-300)
    assert abs(Naira(-300)) == Naira(300)


def test_refuses_arithmetic_with_non_money() -> None:
    with pytest.raises(TypeError, match="unsupported operand"):
        Naira(1) + 1  # type: ignore[operator]
    with pytest.raises(TypeError, match="unsupported operand"):
        Naira(1) - 1  # type: ignore[operator]


def test_format_uses_naira_sign_and_two_decimals() -> None:
    assert Naira.from_naira("1234.5").format() == "₦1,234.50"
    assert Naira.from_naira("-1234.5").format() == "-₦1,234.50"
    assert Naira.zero().format() == "₦0.00"
    assert str(Naira.from_naira("2")) == "₦2.00"


def test_sub_cent_negative_does_not_render_minus_zero() -> None:
    assert Naira(-1).format() == "₦0.00"


def test_sign_helpers() -> None:
    assert Naira(-5).is_negative()
    assert not Naira(5).is_negative()
    assert Naira(0).is_zero()
    assert not Naira(1).is_zero()


def test_round_trip_through_naira() -> None:
    assert Naira.from_naira(Naira(7).to_naira()) == Naira(7)


@given(st.integers(LOW, HIGH), st.integers(LOW, HIGH))
def test_addition_is_exact_and_associative(a: int, b: int) -> None:
    assert Naira(a) + Naira(b) == Naira(a + b)
    assert (Naira(a) + Naira(b)) + Naira(a) == Naira(2 * a + b)


@given(st.integers(LOW, HIGH))
def test_subtraction_inverts_addition(value: int) -> None:
    assert (Naira(value) + Naira(value)) - Naira(value) == Naira(value)
