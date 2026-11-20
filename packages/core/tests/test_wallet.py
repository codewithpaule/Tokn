from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tokn_core.errors import InsufficientFundsError, InvariantViolationError
from tokn_core.ledger import EntryKind, Ledger
from tokn_core.money import Naira
from tokn_core.wallet import Wallet

micro = st.integers(0, 10**12)


@given(st.integers(0, 10**12), st.integers(1, 10**12))
def test_hold_never_leaves_a_negative_balance(opening: int, amount: int) -> None:
    wallet = Wallet(Naira(opening))
    if amount > opening:
        with pytest.raises(InsufficientFundsError) as caught:
            wallet.hold(Naira(amount))
        assert caught.value.required == Naira(amount)
        assert caught.value.available == Naira(opening)
        assert wallet.balance == Naira(opening)
    else:
        wallet.hold(Naira(amount))
        assert wallet.balance == Naira(opening - amount)
    assert not wallet.balance.is_negative()


@given(micro, micro)
def test_settlement_returns_exactly_the_unused_hold(hold: int, charge: int) -> None:
    wallet = Wallet(Naira(hold))
    wallet.hold(Naira(hold))
    assert wallet.balance.is_zero()
    refund = wallet.settle(Naira(hold), Naira(min(charge, hold)))
    assert refund.micro == max(hold - charge, 0)
    assert wallet.balance == refund


def test_settling_above_the_hold_is_refused() -> None:
    wallet = Wallet(Naira(100))
    wallet.hold(Naira(100))
    with pytest.raises(InvariantViolationError, match="exceeds hold"):
        wallet.settle(Naira(100), Naira(101))


def test_negative_amounts_are_refused() -> None:
    wallet = Wallet(Naira(100))
    for operation in (
        lambda: wallet.hold(Naira(-1)),
        lambda: wallet.release(Naira(-1)),
        lambda: wallet.credit(Naira(-1)),
        lambda: wallet.debit(Naira(-1)),
        lambda: wallet.settle(Naira(-1), Naira(0)),
    ):
        with pytest.raises(InvariantViolationError):
            operation()


def test_a_wallet_cannot_be_born_negative() -> None:
    with pytest.raises(InvariantViolationError, match="never be negative"):
        Wallet(Naira(-1))


def test_credit_and_debit_move_the_balance() -> None:
    wallet = Wallet(Naira(1_000))
    wallet.credit(Naira(500))
    wallet.debit(Naira(1_200))
    assert wallet.balance == Naira(300)
    with pytest.raises(InsufficientFundsError):
        wallet.debit(Naira(301))


def test_release_returns_the_whole_hold() -> None:
    wallet = Wallet(Naira(1_000))
    wallet.hold(Naira(400))
    wallet.release(Naira(400))
    assert wallet.balance == Naira(1_000)


@given(st.lists(st.tuples(micro, micro), max_size=30), st.integers(0, 10**11))
def test_repeated_reservations_conserve_money(pairs: list[tuple[int, int]], opening: int) -> None:
    """The flagship invariant: money is never created, never lost, never overdrawn."""
    wallet = Wallet(Naira(opening))
    ledger = Ledger()
    ledger.append(EntryKind.TOPUP, Naira(opening), "topup")

    charged = 0
    for index, (hold, requested) in enumerate(pairs):
        try:
            wallet.hold(Naira(hold))
        except InsufficientFundsError:
            continue
        charge = Naira(min(requested, hold))
        wallet.settle(Naira(hold), charge)
        if not charge.is_zero():
            ledger.append(EntryKind.USAGE, -charge, f"usage-{index}")
        charged += charge.micro

        assert wallet.balance.micro == opening - charged
        assert not wallet.balance.is_negative()
        assert ledger.total().micro == wallet.balance.micro
        assert ledger.is_consistent()


@given(st.lists(st.tuples(micro, micro), max_size=30), st.integers(0, 10**11))
def test_released_requests_leave_the_wallet_untouched(
    pairs: list[tuple[int, int]], opening: int
) -> None:
    wallet = Wallet(Naira(opening))
    for hold, _ in pairs:
        try:
            wallet.hold(Naira(hold))
        except InsufficientFundsError:
            continue
        wallet.release(Naira(hold))
        assert wallet.balance.micro == opening


def test_ledger_refuses_a_duplicate_reference() -> None:
    ledger = Ledger()
    ledger.append(EntryKind.TOPUP, Naira(100), "paystack-ref-1")
    with pytest.raises(InvariantViolationError, match="already recorded"):
        ledger.append(EntryKind.TOPUP, Naira(100), "paystack-ref-1")


def test_ledger_refuses_to_go_negative() -> None:
    ledger = Ledger()
    ledger.append(EntryKind.TOPUP, Naira(100), "topup")
    with pytest.raises(InvariantViolationError):
        ledger.append(EntryKind.USAGE, Naira(-101), "usage")


def test_ledger_replays_to_the_same_balance() -> None:
    ledger = Ledger()
    ledger.append(EntryKind.TOPUP, Naira(50_000), "topup-1")
    ledger.append(EntryKind.USAGE, Naira(-1_250), "usage-1")
    ledger.append(EntryKind.REFUND, Naira(250), "refund-1")
    ledger.append(EntryKind.ADJUSTMENT, Naira(-3), "adjustment-1")
    assert ledger.balance == Naira(48_997)
    assert ledger.total() == ledger.balance
    assert [entry.balance_after.micro for entry in ledger.entries] == [
        50_000,
        48_750,
        49_000,
        48_997,
    ]


def test_ledger_entries_need_a_reference() -> None:
    ledger = Ledger()
    with pytest.raises(InvariantViolationError, match="reference"):
        ledger.append(EntryKind.TOPUP, Naira(1), "")
