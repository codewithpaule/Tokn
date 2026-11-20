from __future__ import annotations

import contextlib

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tokn_core.auth.passwords import MIN_LENGTH, WeakPasswordError, validate_password


def test_a_long_unique_passphrase_is_accepted() -> None:
    validate_password("correct horse battery staple 7")


def test_the_published_floor_is_twelve_characters() -> None:
    assert MIN_LENGTH == 12
    with pytest.raises(WeakPasswordError, match="at least 12"):
        validate_password("elevenchars")


def test_a_very_long_password_is_refused_to_bound_hashing_cost() -> None:
    with pytest.raises(WeakPasswordError, match="at most 256"):
        validate_password("a1B!" * 100)


@pytest.mark.parametrize(
    "password",
    [
        "password1234",
        "PASSWORD1234",
        "Password1234!",
        "qwertyuiop12",
        "letmein12345",
        "iloveyou1234",
    ],
)
def test_common_passwords_are_refused_even_with_decoration(password: str) -> None:
    with pytest.raises(WeakPasswordError, match="breach"):
        validate_password(password)


def test_the_accounts_own_email_is_refused() -> None:
    with pytest.raises(WeakPasswordError, match="email address"):
        validate_password("paul.ruvwu-2026", context="paul.ruvwu@gmail.com")


def test_a_short_email_local_part_is_not_treated_as_a_leak() -> None:
    validate_password("ab-standalone-passphrase", context="ab@example.com")


def test_repetitive_strings_are_refused() -> None:
    with pytest.raises(WeakPasswordError, match="distinct characters"):
        validate_password("aaaaaaaaaaaaaa")


def test_whitespace_only_is_refused() -> None:
    with pytest.raises(WeakPasswordError, match="visible"):
        validate_password("            ")


@given(st.text(min_size=12, max_size=40).filter(lambda value: value.strip() == value))
def test_no_password_ever_raises_something_other_than_the_policy_error(value: str) -> None:
    with contextlib.suppress(WeakPasswordError):
        validate_password(value)
