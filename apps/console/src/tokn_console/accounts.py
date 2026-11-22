"""Account rules the routes share: validation, sessions, sign in, sign out.

Argon2, throttling and session records live in the core; this module only sequences them.
Every failure that could reveal whether an email exists returns the same message.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from fastapi import HTTPException, Request, Response, status

from tokn_console.services import Services
from tokn_console.web import set_session_cookie
from tokn_core.auth.hashing import hash_secret, new_secret
from tokn_core.auth.passwords import WeakPasswordError, validate_password
from tokn_core.auth.sessions import IDLE_TIMEOUT, Session, new_session
from tokn_core.auth.store import AccountRecord
from tokn_core.auth.throttle import ThrottleState, is_over_limit
from tokn_core.errors import AccountExistsError

EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s]{2,24}$")
MAX_EMAIL = 254


class AccountError(Exception):
    """A refusal with a sentence the person can act on."""

    def __init__(self, message: str, *, field: str = "form") -> None:
        super().__init__(message)
        self.message = message
        self.field = field


def normalise_email(raw: str) -> str:
    email = raw.strip().lower()
    if len(email) > MAX_EMAIL or not EMAIL.match(email):
        raise AccountError("Enter a valid email address.", field="email")
    return email


async def account_for(request: Request, services: Services) -> AccountRecord | None:
    session: Session | None = getattr(request.state, "session", None)
    if session is None:
        return None
    return await services.auth.account_by_id(session.account_id)


async def require_account(request: Request, services: Services) -> AccountRecord:
    account = await account_for(request, services)
    if account is None:
        target = request.url.path
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"location": f"/login?next={target}"},
        )
    return account


async def sign_in(
    request: Request,
    response: Response,
    services: Services,
    account: AccountRecord,
) -> None:
    """Mint a session, store its digest, and set the cookie."""
    now: datetime = services.clock.now()
    raw, _session = new_session(
        account.id,
        now,
        request.client.host if request.client else None,
        request.headers.get("user-agent"),
    )
    digest = hash_secret(raw, services.settings.session_secret)
    await services.auth.create_session(
        digest,
        account.id,
        now,
        request.client.host if request.client else None,
        request.headers.get("user-agent"),
    )
    # Sessions are rotated on every login. The idle timeout is the cookie's lifetime.
    set_session_cookie(
        response,
        raw,
        max_age=int(IDLE_TIMEOUT.total_seconds()),
        secure=services.secure_cookies,
        cookie_name=services.session_cookie,
    )


async def sign_out(request: Request, response: Response, services: Services) -> None:
    digest = getattr(request.state, "session_digest", None)
    if digest:
        await services.auth.delete_session(digest)
    response.delete_cookie(
        services.session_cookie,
        path="/",
        secure=services.secure_cookies,
        httponly=True,
        samesite="lax",
    )


async def register(
    services: Services,
    *,
    email: str,
    password: str,
    confirmation: str,
    ip: str,
) -> AccountRecord:
    now = services.clock.now()
    gate = f"signup|{ip}"
    attempts = await services.auth.throttle(gate)
    if is_over_limit(attempts, now, limit=5):
        raise AccountError(
            "Too many accounts were created from this address. Try again later.", field="form"
        )

    cleaned = normalise_email(email)
    if password != confirmation:
        raise AccountError("The two passwords do not match.", field="password")
    try:
        validate_password(password, context=cleaned)
    except WeakPasswordError as error:
        raise AccountError(str(error), field="password") from error

    hashed = services.hasher.hash(password)
    try:
        created = await services.auth.create_account(cleaned, hashed)
    except AccountExistsError as error:
        await services.auth.save_throttle(gate, attempts.with_failure(now))
        raise AccountError(
            "That email already has an account. Sign in instead.", field="email"
        ) from error
    await services.auth.save_throttle(gate, attempts.with_failure(now))

    # There is no mail worker in this deployment yet. Auto-activation keeps the product
    # usable; email ownership is still unproven, so the account starts with no credit and
    # no admin rights. An operator can suspend it from the database at any time.
    await services.auth.set_email_verified(created.id, now)
    if cleaned in services.settings.administrators:
        await services.auth.set_admin(created.id, True)
    elif services.settings.env == "development":
        # Local development has no admin list to seed. Production never takes this path:
        # TOKN_ENV must not be development there, and validated() then demands real secrets.
        await services.auth.set_admin(created.id, True)
    account = await services.auth.account_by_id(created.id)
    if account is None:  # pragma: no cover - the row was just written
        raise AccountError("The account could not be created. Try again.")
    await services.dashboard.ensure_wallet(account.id)
    await services.auth.enqueue_email(
        cleaned,
        f"Welcome to {services.settings.product_name}",
        "Your account is ready. Create an API key from the dashboard when you are.",
    )
    return account


async def authenticate(
    services: Services,
    *,
    email: str,
    password: str,
    ip: str,
) -> AccountRecord:
    cleaned = email.strip().lower()
    identity = f"{ip}|{cleaned}"
    now = services.clock.now()
    state = await services.auth.throttle(identity)
    if state.is_locked(now):
        wait = state.retry_after(now)
        seconds = max(1, int(wait.total_seconds() + 0.999))
        raise AccountError(
            f"Too many attempts. Wait {seconds} seconds and try again.", field="form"
        )

    account = await services.auth.account_by_email(cleaned)
    if account is None or not account.password_hash:
        await _failed(services, identity, state, now)
        raise AccountError("Email or password is not correct.", field="form")
    if not services.hasher.verify(password, account.password_hash):
        await _failed(services, identity, state, now)
        raise AccountError("Email or password is not correct.", field="form")
    if account.status == "suspended":
        raise AccountError("This account is suspended. Contact support.", field="form")
    if not account.can_log_in:
        raise AccountError("Check your email and verify the account first.", field="form")

    if services.hasher.needs_rehash(account.password_hash):
        await services.auth.set_password_hash(account.id, services.hasher.hash(password))
    await services.auth.save_throttle(identity, ThrottleState())
    return account


async def _failed(
    services: Services, identity: str, state: ThrottleState, now: datetime
) -> None:
    updated = state.with_failure(now)
    await services.auth.save_throttle(identity, updated)
    # A tiny constant delay makes scripted guessing cost something without holding a
    # worker for the full exponential backoff, which the next request will enforce.
    import asyncio

    await asyncio.sleep(min(0.25, updated.retry_after(now).total_seconds()))


async def change_password(
    services: Services,
    *,
    account: AccountRecord,
    current: str,
    new: str,
    confirmation: str,
    keep_digest: str | None,
) -> None:
    if not account.password_hash or not services.hasher.verify(current, account.password_hash):
        raise AccountError("The current password is not correct.", field="current")
    if new != confirmation:
        raise AccountError("The two new passwords do not match.", field="new")
    try:
        validate_password(new, context=account.email)
    except WeakPasswordError as error:
        raise AccountError(str(error), field="new") from error
    await services.auth.set_password_hash(account.id, services.hasher.hash(new))
    await services.auth.delete_sessions(account.id, keep=keep_digest)


def session_expiry(services: Services) -> datetime:
    return services.clock.now() + timedelta(seconds=int(IDLE_TIMEOUT.total_seconds()))


def new_csrf() -> str:
    return new_secret(32)
