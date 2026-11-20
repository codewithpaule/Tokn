"""API key format, generation and rules.

A key looks like ``tokn_live_4f3a...`` — a configurable product prefix, an environment
marker, and 256 bits of hex. The raw value is shown once. We store the visible prefix
(for support and for GitHub secret scanning) and an HMAC-SHA256 digest, never the key.

Hex is used for the secret body on purpose: base64url can emit ``-`` and ``_``, which
would make the key ambiguous to split and to copy.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime

from tokn_core.auth.hashing import digests_match, hash_secret
from tokn_core.errors import DomainError

KEY_BYTES = 32
VISIBLE_PREFIX_CHARS = 6
ENVIRONMENTS = frozenset({"live", "test"})
REQUIRED_SCOPE = "infer"


class KeyFormatError(DomainError):
    """The presented string is not shaped like a Tokn key."""


@dataclass(frozen=True, slots=True)
class GeneratedKey:
    """``raw`` is shown to the customer once; ``prefix`` and ``digest`` are stored."""

    raw: str
    prefix: str
    digest: str


@dataclass(frozen=True, slots=True)
class KeyRecord:
    """Everything needed to authorise a request, without the secret."""

    id: str
    account_id: str
    prefix: str
    digest: str
    scopes: frozenset[str]
    spent_micro: int = 0
    spend_cap_micro: int | None = None
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    ip_allowlist: tuple[str, ...] | None = None
    auto_paused: bool = False
    concurrency_limit: int = 8
    name: str = ""
    last_used_at: datetime | None = None

    def is_usable(self, now: datetime) -> bool:
        """Cheap checks that need no request context. All failures look identical."""
        if self.revoked_at is not None or self.auto_paused:
            return False
        if self.expires_at is not None and now >= self.expires_at:
            return False
        if self.spend_cap_micro is not None:
            return self.spent_micro < self.spend_cap_micro
        return True

    def permits(self, ip: str | None) -> bool:
        if self.ip_allowlist is None:
            return True
        return ip is not None and ip in self.ip_allowlist

    def has_scope(self, scope: str = REQUIRED_SCOPE) -> bool:
        return scope in self.scopes


def generate_key(*, pepper: str, product: str = "tokn", environment: str = "live") -> GeneratedKey:
    """Mint one key. The caller must show ``raw`` and then forget it."""
    if environment not in ENVIRONMENTS:
        raise KeyFormatError(f"unknown environment: {environment}")
    if not product or "_" in product:
        raise KeyFormatError("product prefix must be non-empty and contain no underscore")
    body = secrets.token_hex(KEY_BYTES)
    raw = f"{product}_{environment}_{body}"
    return GeneratedKey(
        raw=raw,
        prefix=f"{product}_{environment}_{body[:VISIBLE_PREFIX_CHARS]}",
        digest=hash_secret(raw, pepper),
    )


def parse_prefix(raw: str, *, product: str = "tokn") -> str | None:
    """Return the visible prefix of a well-shaped key, or None. Makes no I/O."""
    parts = raw.split("_")
    if len(parts) != 3:
        return None
    head, environment, body = parts
    if head != product or environment not in ENVIRONMENTS:
        return None
    if len(body) != KEY_BYTES * 2 or any(ch not in "0123456789abcdef" for ch in body):
        return None
    return f"{head}_{environment}_{body[:VISIBLE_PREFIX_CHARS]}"


def digest_of(raw: str, pepper: str) -> str:
    return hash_secret(raw, pepper)


def matches(raw: str, record: KeyRecord, pepper: str) -> bool:
    """Constant-time check that this raw key is the one behind ``record``."""
    return digests_match(digest_of(raw, pepper), record.digest)


def redact(prefix: str) -> str:
    """For logs and support screens: enough to identify, never enough to use."""
    return f"{prefix}…"
