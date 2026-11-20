"""Time-based one-time passwords (RFC 6238), implemented on the standard library.

A six-digit code that changes every 30 seconds. Verification accepts one step either
side to tolerate clock skew, and compares in constant time.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
from datetime import datetime, timedelta
from urllib.parse import quote, urlencode

STEP_SECONDS = 30
DIGITS = 6
SECRET_BYTES = 20
WINDOW = 1


def generate_secret() -> str:
    """A fresh, unguessable base32 secret, ready for an authenticator app."""
    return base64.b32encode(secrets.token_bytes(SECRET_BYTES)).decode("ascii")


def _decode(secret: str) -> bytes:
    padded = secret.upper() + "=" * (-len(secret) % 8)
    try:
        return base64.b32decode(padded, casefold=True)
    except Exception as error:
        raise ValueError("secret is not valid base32") from error


def _counter(at: datetime) -> int:
    return int(at.timestamp()) // STEP_SECONDS


def code_at(secret: str, at: datetime) -> str:
    """The six-digit code valid during the step that contains ``at``."""
    digest = hmac.new(_decode(secret), struct.pack(">Q", _counter(at)), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return f"{value % 10**DIGITS:0{DIGITS}d}"


def verify(secret: str, code: str, at: datetime, window: int = WINDOW) -> bool:
    """True when ``code`` matches the current step or one of ``window`` neighbours."""
    candidate = code.strip().replace(" ", "")
    if len(candidate) != DIGITS or not candidate.isdigit():
        return False
    for offset in range(-window, window + 1):
        step_at = at + timedelta(seconds=offset * STEP_SECONDS)
        if hmac.compare_digest(code_at(secret, step_at), candidate):
            return True
    return False


def provisioning_uri(secret: str, account_email: str, issuer: str) -> str:
    """The ``otpauth://`` URI an authenticator app expects to scan."""
    label = quote(f"{issuer}:{account_email}", safe="")
    query = urlencode(
        {
            "secret": secret,
            "issuer": issuer,
            "algorithm": "SHA1",
            "digits": DIGITS,
            "period": STEP_SECONDS,
        }
    )
    return f"otpauth://totp/{label}?{query}"
