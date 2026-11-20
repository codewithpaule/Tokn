"""Argon2id itself: the one place a real, slow hash is exercised."""

from __future__ import annotations

import pytest

from tokn_infra.security import Argon2PasswordHasher


def test_hash_and_verify_round_trip() -> None:
    hasher = Argon2PasswordHasher()
    hashed = hasher.hash("a-long-passphrase-2026")

    assert hashed.startswith("$argon2id$")
    assert hasher.verify("a-long-passphrase-2026", hashed)
    assert not hasher.verify("a-long-passphrase-2027", hashed)


def test_the_same_password_hashes_differently_every_time() -> None:
    hasher = Argon2PasswordHasher()
    first = hasher.hash("a-long-passphrase-2026")
    second = hasher.hash("a-long-passphrase-2026")

    assert first != second, "each hash must carry its own salt"
    assert hasher.verify("a-long-passphrase-2026", first)
    assert hasher.verify("a-long-passphrase-2026", second)


@pytest.mark.parametrize("broken", ["", "not-a-hash", "$argon2id$v=19$m=1$bad", "fast$abc"])
def test_a_malformed_hash_returns_false_instead_of_raising(broken: str) -> None:
    assert Argon2PasswordHasher().verify("whatever", broken) is False


def test_old_parameters_are_flagged_for_rehash() -> None:
    weak = Argon2PasswordHasher(memory_kib=8_192, time_cost=1, parallelism=1)
    strong = Argon2PasswordHasher()
    hashed = weak.hash("a-long-passphrase-2026")

    assert weak.needs_rehash(hashed) is False
    assert strong.needs_rehash(hashed) is True
    assert strong.verify("a-long-passphrase-2026", hashed), "an old hash still verifies"


def test_parameters_below_the_floor_are_refused() -> None:
    with pytest.raises(ValueError, match="8 MiB"):
        Argon2PasswordHasher(memory_kib=1024)
    with pytest.raises(ValueError, match="at least 1"):
        Argon2PasswordHasher(time_cost=0)
