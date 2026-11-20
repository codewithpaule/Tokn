from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tokn_core.auth.sessions import (
    ABSOLUTE_TIMEOUT,
    IDLE_TIMEOUT,
    SessionExpiredError,
    new_session,
)

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)


def make_session() -> tuple[str, object]:
    return new_session("account-1", NOW, ip="203.0.113.9", user_agent="pytest")


def test_a_new_session_is_live() -> None:
    raw, session = make_session()
    assert len(raw) >= 43
    assert not session.is_expired(NOW)


def test_thirty_minutes_of_silence_ends_it() -> None:
    _, session = make_session()
    assert not session.is_expired(NOW + IDLE_TIMEOUT - timedelta(seconds=1))
    assert session.is_expired(NOW + IDLE_TIMEOUT)


def test_activity_does_not_extend_it_past_seven_days() -> None:
    _, session = make_session()
    active = session
    for hour in range(1, 24 * 8):
        moment = NOW + timedelta(hours=hour)
        if active.is_expired(moment):
            break
        active = active.touch(moment)

    assert active.is_expired(NOW + ABSOLUTE_TIMEOUT)
    assert timedelta(days=7) == ABSOLUTE_TIMEOUT


def test_touch_records_activity() -> None:
    _, session = make_session()
    later = NOW + timedelta(minutes=10)
    assert session.touch(later).last_seen_at == later


def test_touch_refuses_to_revive_an_expired_session() -> None:
    _, session = make_session()
    with pytest.raises(SessionExpiredError):
        session.touch(NOW + IDLE_TIMEOUT)


def test_expiry_is_the_earlier_of_idle_and_absolute() -> None:
    _, session = make_session()
    assert session.expires_at(NOW) == NOW + IDLE_TIMEOUT


def test_two_sessions_never_share_an_id() -> None:
    ids = {new_session("account-1", NOW, None, None)[0] for _ in range(200)}
    assert len(ids) == 200


def test_the_session_id_carries_at_least_256_bits() -> None:
    raw, _ = make_session()
    assert len(raw) * 6 >= 256  # base64url: six bits per character
