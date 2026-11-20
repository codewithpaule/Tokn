"""Argon2id password hashing.

Parameters follow OWASP's Argon2id guidance: 19 MiB of memory, two iterations, one lane.
They are constructor arguments so a deployment can raise them, and ``needs_rehash`` lets
login transparently upgrade an old hash.
"""

from __future__ import annotations

from argon2 import PasswordHasher as _Argon2Hasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

DEFAULT_MEMORY_KIB = 19_456
DEFAULT_TIME_COST = 2
DEFAULT_PARALLELISM = 1


class Argon2PasswordHasher:
    """Implements ``tokn_core.auth.ports.PasswordHasher``."""

    def __init__(
        self,
        *,
        memory_kib: int = DEFAULT_MEMORY_KIB,
        time_cost: int = DEFAULT_TIME_COST,
        parallelism: int = DEFAULT_PARALLELISM,
    ) -> None:
        if memory_kib < 8_192:
            raise ValueError("refusing to hash with less than 8 MiB of memory")
        if time_cost < 1 or parallelism < 1:
            raise ValueError("time_cost and parallelism must be at least 1")
        self._hasher = _Argon2Hasher(
            memory_cost=memory_kib,
            time_cost=time_cost,
            parallelism=parallelism,
            hash_len=32,
            salt_len=16,
        )

    def hash(self, password: str) -> str:
        return self._hasher.hash(password)

    def verify(self, password: str, hashed: str) -> bool:
        try:
            return self._hasher.verify(hashed, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False

    def needs_rehash(self, hashed: str) -> bool:
        try:
            return self._hasher.check_needs_rehash(hashed)
        except (InvalidHashError, VerificationError):
            return True
