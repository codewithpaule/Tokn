from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tokn_core.errors import InvariantViolationError
from tokn_core.money import Naira
from tokn_core.reservations import Reservation, ReservationStatus

micro = st.integers(0, 10**12)


def test_a_new_reservation_is_open() -> None:
    reservation = Reservation(request_id="req_1", hold=Naira(500))
    assert reservation.is_open
    assert reservation.status is ReservationStatus.HELD
    assert reservation.charge is None


@given(micro, micro)
def test_settlement_is_idempotent(hold: int, charge: int) -> None:
    reservation = Reservation(request_id="req_1", hold=Naira(hold))
    paid = Naira(min(charge, hold))

    first = reservation.settle(paid)
    second = reservation.settle(paid)
    third = reservation.settle(paid)

    assert first == second == third
    assert first.micro == hold - paid.micro
    assert first.micro >= 0
    assert reservation.status is ReservationStatus.SETTLED
    assert reservation.charge == paid


@given(micro, micro)
def test_hold_minus_charge_is_never_negative(hold: int, charge: int) -> None:
    reservation = Reservation(request_id="req_1", hold=Naira(hold))
    refund = reservation.settle(Naira(min(charge, hold)))
    assert refund.micro >= 0
    assert refund.micro == hold - min(charge, hold)


@given(micro, micro, micro)
def test_settling_twice_with_different_amounts_is_a_bug(hold: int, first: int, second: int) -> None:
    reservation = Reservation(request_id="req_1", hold=Naira(hold))
    paid = Naira(min(first, hold))
    reservation.settle(paid)
    if min(second, hold) == paid.micro:
        assert reservation.settle(paid) == Naira(hold - paid.micro)
        return
    with pytest.raises(InvariantViolationError, match="already settled"):
        reservation.settle(Naira(min(second, hold)))


def test_a_charge_above_the_hold_is_refused() -> None:
    reservation = Reservation(request_id="req_1", hold=Naira(100))
    with pytest.raises(InvariantViolationError, match="exceeds hold"):
        reservation.settle(Naira(101))
    assert reservation.is_open


def test_a_negative_charge_is_refused() -> None:
    reservation = Reservation(request_id="req_1", hold=Naira(100))
    with pytest.raises(InvariantViolationError, match="must not be negative"):
        reservation.settle(Naira(-1))
    assert reservation.is_open


def test_release_returns_the_hold_and_is_idempotent() -> None:
    reservation = Reservation(request_id="req_1", hold=Naira(100))
    reservation.release()
    reservation.release()
    assert reservation.status is ReservationStatus.RELEASED
    assert reservation.charge is None


def test_a_released_reservation_cannot_be_settled() -> None:
    reservation = Reservation(request_id="req_1", hold=Naira(100))
    reservation.release()
    with pytest.raises(InvariantViolationError, match="released"):
        reservation.settle(Naira(10))


def test_a_settled_reservation_cannot_be_released() -> None:
    reservation = Reservation(request_id="req_1", hold=Naira(100))
    reservation.settle(Naira(90))
    with pytest.raises(InvariantViolationError, match="settled"):
        reservation.release()


def test_a_reservation_needs_an_identity() -> None:
    with pytest.raises(InvariantViolationError, match="request id"):
        Reservation(request_id="", hold=Naira(1))


def test_a_hold_cannot_be_negative() -> None:
    with pytest.raises(InvariantViolationError, match="not be negative"):
        Reservation(request_id="req_1", hold=Naira(-1))


@given(st.lists(st.tuples(micro, micro), max_size=25))
def test_every_reservation_reaches_exactly_one_terminal_state(
    pairs: list[tuple[int, int]],
) -> None:
    for index, (hold, charge) in enumerate(pairs):
        reservation = Reservation(request_id=f"req_{index}", hold=Naira(hold))
        if index % 3 == 2:
            reservation.release()
            assert reservation.status is ReservationStatus.RELEASED
        else:
            reservation.settle(Naira(min(charge, hold)))
            assert reservation.status is ReservationStatus.SETTLED
        assert not reservation.is_open
