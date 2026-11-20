"""Secret generation and one-way hashing for anything the server must not be able to read.

Session ids, API keys, email tokens and recovery codes follow the same rule: generate
256 bits of randomness, hand the raw value to the user once, and store only
HMAC-SHA256(value, pepper). A database leak therefore yields no usable credential.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

TOKEN_BYTES = 32


def new_secret(nbytes: int = TOKEN_BYTES) -> str:
    """A URL-safe secret with at least 256 bits of entropy."""
    return secrets.token_urlsafe(nbytes)


def hash_secret(value: str, pepper: str) -> str:
    """Deterministic, keyed digest. The pepper is a server-side secret."""
    if not pepper:
        raise ValueError("a pepper is required to hash secrets")
    return hashlib.sha256(pepper.encode("utf-8") + value.encode("utf-8")).hexdigest()


def digests_match(left: str, right: str) -> bool:
    """Constant-time comparison, safe to use on attacker-supplied input."""
    return hmac.compare_digest(left, right)
