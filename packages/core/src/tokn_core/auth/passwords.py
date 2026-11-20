"""Password policy.

Length does most of the work, so the floor is 12 characters. A bundled list stops the
handful of passwords that appear at the top of every breach corpus, and the account's own
email address is refused because it is the first thing an attacker tries.

Argon2id hashing itself lives behind the ``PasswordHasher`` port in
``tokn_core.auth.ports``; this module only decides whether a password is acceptable.
"""

from __future__ import annotations

from tokn_core.errors import DomainError

MIN_LENGTH = 12
MAX_LENGTH = 256

_COMMON_PASSWORDS = frozenset(
    [
        "123456",
        "12345678",
        "123456789",
        "1234567890",
        "111111",
        "123123",
        "000000",
        "654321",
        "121212",
        "112233",
        "password",
        "password1",
        "password12",
        "password123",
        "passw0rd",
        "p@ssw0rd",
        "p@ssword",
        "pass1234",
        "qwerty",
        "qwerty123",
        "qwertyuiop",
        "1q2w3e4r",
        "1qaz2wsx",
        "zaq12wsx",
        "asdfghjkl",
        "zxcvbnm",
        "abc123",
        "abcdef",
        "abcd1234",
        "a1b2c3d4",
        "iloveyou",
        "admin",
        "administrator",
        "root",
        "toor",
        "letmein",
        "welcome",
        "welcome1",
        "welcome123",
        "monkey",
        "dragon",
        "sunshine",
        "princess",
        "football",
        "baseball",
        "superman",
        "batman",
        "starwars",
        "whatever",
        "trustno1",
        "master",
        "shadow",
        "michael",
        "jennifer",
        "jordan",
        "hunter2",
        "harley",
        "davidson",
        "mustang",
        "ferrari",
        "liverpool",
        "arsenal",
        "chelsea",
        "barcelona",
        "chocolate",
        "cookie",
        "computer",
        "internet",
        "samsung",
        "google",
        "facebook",
        "linkedin",
        "twitter",
        "secret",
        "secret123",
        "mypassword",
        "mypass",
        "mysecret",
        "changeme",
        "change_me",
        "default",
        "temp123",
        "test1234",
        "testing",
        "qwerty1",
        "qwerty12",
        "qwerty1234",
        "qazwsx",
        "qazwsxedc",
        "1qazxsw2",
        "letmein123",
        "iloveyou1",
        "michelle",
        "nicole",
        "jessica",
        "charlie",
        "donald",
        "hockey",
        "soccer",
        "killer",
        "george",
        "summer",
        "winter",
        "spring",
        "autumn",
        "january",
        "february",
        "december",
        "nigerian",
        "nairaland",
        "lagos",
        "abuja",
        "naija",
        "nigeria123",
        "jollof",
        "buhari",
        "tinubu",
    ]
)


class WeakPasswordError(DomainError):
    """The password does not meet the floor we publish."""


def _looks_repetitive(candidate: str) -> bool:
    return len(set(candidate)) <= 2


def validate_password(password: str, *, context: str = "") -> None:
    """Raise :class:`WeakPasswordError` with a reason the user can act on."""
    if len(password) < MIN_LENGTH:
        raise WeakPasswordError(f"use at least {MIN_LENGTH} characters")
    if len(password) > MAX_LENGTH:
        raise WeakPasswordError(f"use at most {MAX_LENGTH} characters")
    if not password.strip():
        raise WeakPasswordError("use at least one visible character")

    lowered = password.lower()
    stem = lowered.rstrip("0123456789!@#$")
    if lowered in _COMMON_PASSWORDS or stem in _COMMON_PASSWORDS:
        raise WeakPasswordError("this password appears in public breach lists")
    if _looks_repetitive(lowered):
        raise WeakPasswordError("use more than a couple of distinct characters")

    if context:
        local_part = context.split("@", 1)[0].lower()
        if len(local_part) >= 3 and local_part in lowered:
            raise WeakPasswordError("do not use your email address in your password")
