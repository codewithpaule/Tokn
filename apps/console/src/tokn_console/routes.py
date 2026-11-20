"""Authentication routes: signup, verification, login, reset, two-factor.

Every error path returns the same shape, so an attacker cannot tell a registered address
from an unregistered one. Every state change rotates or destroys the session.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from starlette.templating import Jinja2Templates

from tokn_console.config import Settings
from tokn_console.web import (
    CSRF_COOKIE,
    clear_session_cookie,
    client_ip,
    require_csrf,
    set_session_cookie,
)
from tokn_core.api_keys import KeyRecord
from tokn_core.auth.hashing import hash_secret, new_secret
from tokn_core.auth.passwords import WeakPasswordError, validate_password
from tokn_core.auth.ports import Clock, PasswordHasher
from tokn_core.auth.sessions import IDLE_TIMEOUT, new_session
from tokn_core.auth.store import AccountRecord, AuthStore
from tokn_core.auth.throttle import ThrottleState, is_over_limit
from tokn_core.auth.tokens import TokenKind, issue_token, new_recovery_codes
from tokn_core.auth.totp import generate_secret, provisioning_uri, verify
from tokn_core.errors import AccountExistsError
from tokn_core.key_auth import (
    KeyCache,
    KeyIssuanceRefusedError,
    KeyStore,
    NewKeyRequest,
    issue_key,
    list_keys,
    revoke_key,
)
from tokn_core.money import Naira

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
DUMMY_HASH = "$argon2id$v=19$m=19456,t=2,p=1$c29tZXNhbHRzYWx0$" + "0" * 43
RESET_LIMIT = 5

GENERIC_SIGNUP = "If that address can be used, a verification link is on its way."
GENERIC_RESET = "If that address has an account, a reset link is on its way."


def build_router(
    *,
    store: AuthStore,
    key_store: KeyStore,
    key_cache: KeyCache,
    hasher: PasswordHasher,
    clock: Clock,
    settings: Settings,
    templates: Jinja2Templates,
) -> APIRouter:
    router = APIRouter()

    def render(
        request: Request, template: str, *, status_code: int = 200, **context: object
    ) -> Response:
        token = request.cookies.get(CSRF_COOKIE) or new_secret(32)
        body = {**context, "csrf_token": token, "product": settings.product_name}
        response = templates.TemplateResponse(request, template, body, status_code=status_code)
        if request.cookies.get(CSRF_COOKIE) is None:
            response.set_cookie(
                CSRF_COOKIE, token, httponly=True, secure=True, samesite="lax", path="/"
            )
        return response

    async def current_account(request: Request) -> AccountRecord | None:
        session = getattr(request.state, "session", None)
        if session is None:
            return None
        return await store.account_by_id(session.account_id)

    async def start_session(request: Request, response: Response, account_id: str) -> None:
        now = clock.now()
        raw, _ = new_session(account_id, now, client_ip(request), request.headers.get("user-agent"))
        await store.create_session(
            hash_secret(raw, settings.session_secret),
            account_id,
            now,
            client_ip(request),
            request.headers.get("user-agent"),
        )
        previous = request.cookies.get("__Host-session")
        if previous:
            await store.delete_session(hash_secret(previous, settings.session_secret))
        set_session_cookie(
            response,
            raw,
            max_age=int(IDLE_TIMEOUT.total_seconds()),
            secure=settings.secure_cookies,
        )

    def locked_response(request: Request, state: ThrottleState) -> Response:
        wait = state.retry_after(clock.now())
        return render(
            request,
            "message.html",
            status_code=429,
            heading="Too many attempts",
            body=f"Try again in {max(1, int(wait.total_seconds()))} seconds.",
        )

    async def record_failure(email: str, ip: str) -> None:
        now = clock.now()
        for identity in (f"email:{email}", f"ip:{ip}"):
            await store.save_throttle(identity, (await store.throttle(identity)).with_failure(now))

    # ---------------------------------------------------------------- signup

    @router.get("/signup")
    async def signup_form(request: Request) -> Response:
        return render(request, "signup.html")

    @router.post("/signup", dependencies=[Depends(require_csrf)])
    async def signup(
        request: Request,
        email: str = Form(""),
        password: str = Form(""),
    ) -> Response:
        normalised = email.strip().lower()
        if not EMAIL_RE.match(normalised):
            return render(
                request, "signup.html", status_code=422, error="Use a valid email address."
            )
        try:
            validate_password(password, context=normalised)
        except WeakPasswordError as error:
            return render(request, "signup.html", status_code=422, error=str(error))

        try:
            account = await store.create_account(normalised, hasher.hash(password))
        except AccountExistsError:
            return render(request, "message.html", heading="Check your email", body=GENERIC_SIGNUP)

        token = issue_token(TokenKind.EMAIL_VERIFICATION, clock.now(), settings.session_secret)
        await store.issue_token(account.id, token.digest, token.kind, token.expires_at)
        link = f"{settings.console_url}/verify?token={token.raw}"
        await store.enqueue_email(
            normalised,
            "Verify your email",
            f"Open this link within 30 minutes: {link}",
        )
        return render(request, "message.html", heading="Check your email", body=GENERIC_SIGNUP)

    @router.get("/verify")
    async def verify_email(request: Request, token: str = "") -> Response:
        digest = hash_secret(token, settings.session_secret) if token else ""
        account_id = await store.consume_token(digest, TokenKind.EMAIL_VERIFICATION, clock.now())
        if account_id is None:
            return render(
                request,
                "message.html",
                status_code=400,
                heading="Link not valid",
                body="This link has expired or has already been used. Request a new one.",
            )
        await store.set_email_verified(account_id, clock.now())
        return render(
            request,
            "message.html",
            heading="Email verified",
            body="You can sign in now.",
        )

    # ----------------------------------------------------------------- login

    @router.get("/login")
    async def login_form(request: Request) -> Response:
        return render(request, "login.html")

    @router.post("/login", dependencies=[Depends(require_csrf)])
    async def login(
        request: Request,
        email: str = Form(""),
        password: str = Form(""),
        code: str = Form(""),
    ) -> Response:
        normalised = email.strip().lower()
        ip = client_ip(request)
        email_state = await store.throttle(f"email:{normalised}")
        ip_state = await store.throttle(f"ip:{ip}")
        if email_state.is_locked(clock.now()) or ip_state.is_locked(clock.now()):
            return locked_response(request, max([email_state, ip_state], key=lambda s: s.failures))

        account = await store.account_by_email(normalised)
        if account is None or account.password_hash is None:
            hasher.verify(password, DUMMY_HASH)  # equalise timing
            await record_failure(normalised, ip)
            return render(
                request, "login.html", status_code=401, error="Email or password is incorrect."
            )

        if not hasher.verify(password, account.password_hash):
            await record_failure(normalised, ip)
            return render(
                request, "login.html", status_code=401, error="Email or password is incorrect."
            )

        if not await _second_factor_ok(store, account, code, clock.now(), settings.session_secret):
            await record_failure(normalised, ip)
            return render(
                request,
                "login.html",
                status_code=401,
                error="Email, password or code is incorrect.",
                needs_code=True,
            )

        if not account.can_log_in:
            return render(
                request,
                "message.html",
                status_code=403,
                heading="Account not active",
                body="Verify your email address before signing in.",
            )

        for identity in (f"email:{normalised}", f"ip:{ip}"):
            await store.save_throttle(identity, ThrottleState())

        response = RedirectResponse("/app", status_code=303)
        await start_session(request, response, account.id)
        return response

    @router.post("/logout", dependencies=[Depends(require_csrf)])
    async def logout(request: Request) -> Response:
        raw = request.cookies.get("__Host-session")
        if raw:
            await store.delete_session(hash_secret(raw, settings.session_secret))
        response = RedirectResponse("/login", status_code=303)
        clear_session_cookie(response, secure=settings.secure_cookies)
        return response

    # ----------------------------------------------------------------- reset

    @router.get("/reset")
    async def reset_form(request: Request) -> Response:
        return render(request, "reset.html")

    @router.post("/reset", dependencies=[Depends(require_csrf)])
    async def reset_request(request: Request, email: str = Form("")) -> Response:
        normalised = email.strip().lower()
        ip = client_ip(request)
        state = await store.throttle(f"ip:{ip}:reset")
        if is_over_limit(state, clock.now(), limit=RESET_LIMIT):
            return locked_response(request, state)
        await store.save_throttle(f"ip:{ip}:reset", state.with_failure(clock.now()))

        account = await store.account_by_email(normalised) if EMAIL_RE.match(normalised) else None
        if account is not None:
            token = issue_token(TokenKind.PASSWORD_RESET, clock.now(), settings.session_secret)
            await store.issue_token(account.id, token.digest, token.kind, token.expires_at)
            link = f"{settings.console_url}/reset/confirm?token={token.raw}"
            await store.enqueue_email(
                normalised, "Reset your password", f"Open this link within 30 minutes: {link}"
            )
        return render(request, "message.html", heading="Check your email", body=GENERIC_RESET)

    @router.get("/reset/confirm")
    async def reset_confirm_form(request: Request, token: str = "") -> Response:
        return render(request, "reset_confirm.html", token=token)

    @router.post("/reset/confirm", dependencies=[Depends(require_csrf)])
    async def reset_confirm(
        request: Request, token: str = Form(""), password: str = Form("")
    ) -> Response:
        try:
            validate_password(password)
        except WeakPasswordError as error:
            return render(
                request, "reset_confirm.html", status_code=422, token=token, error=str(error)
            )
        digest = hash_secret(token, settings.session_secret) if token else ""
        account_id = await store.consume_token(digest, TokenKind.PASSWORD_RESET, clock.now())
        if account_id is None:
            return render(
                request,
                "message.html",
                status_code=400,
                heading="Link not valid",
                body="This link has expired or has already been used.",
            )
        await store.set_password_hash(account_id, hasher.hash(password))
        await store.delete_sessions(account_id)
        return render(
            request,
            "message.html",
            heading="Password changed",
            body="Sign in with your new password.",
        )

    # ------------------------------------------------------------- two-factor

    @router.get("/app/2fa")
    async def two_factor_form(request: Request) -> Response:
        account = await current_account(request)
        if account is None:
            return RedirectResponse("/login", status_code=303)
        secret = account.totp_secret or generate_secret()
        return render(
            request,
            "two_factor.html",
            secret=secret,
            uri=provisioning_uri(secret, account.email, settings.product_name),
        )

    @router.post("/app/2fa", dependencies=[Depends(require_csrf)])
    async def two_factor_enable(
        request: Request, secret: str = Form(""), code: str = Form("")
    ) -> Response:
        account = await current_account(request)
        if account is None:
            return RedirectResponse("/login", status_code=303)
        if not verify(secret, code, clock.now()):
            return render(
                request,
                "two_factor.html",
                status_code=422,
                secret=secret,
                uri=provisioning_uri(secret, account.email, settings.product_name),
                error="That code did not match. Check your authenticator and try again.",
            )
        await store.set_totp_secret(account.id, secret)
        codes = new_recovery_codes(settings.session_secret)
        await store.set_recovery_codes(account.id, tuple(digest for _, digest in codes))
        return render(request, "recovery_codes.html", codes=[raw for raw, _ in codes])

    # ------------------------------------------------------------------- app

    @router.get("/app")
    async def overview(request: Request) -> Response:
        account = await current_account(request)
        if account is None:
            return RedirectResponse("/login", status_code=303)
        return render(request, "app.html", account=account)

    # ------------------------------------------------------------------ keys

    def keys_view(
        request: Request,
        account: AccountRecord,
        keys: tuple[KeyRecord, ...],
        *,
        error: str | None = None,
        status_code: int = 200,
    ) -> Response:
        return render(
            request,
            "keys.html",
            status_code=status_code,
            account=account,
            keys=keys,
            enrolled=account.totp_secret is not None,
            error=error,
        )

    def step_up_ok(account: AccountRecord, code: str) -> bool:
        return account.totp_secret is not None and verify(account.totp_secret, code, clock.now())

    @router.get("/app/keys")
    async def keys_page(request: Request) -> Response:
        account = await current_account(request)
        if account is None:
            return RedirectResponse("/login", status_code=303)
        return keys_view(request, account, await list_keys(key_store, account.id))

    @router.post("/app/keys", dependencies=[Depends(require_csrf)])
    async def create_key(
        request: Request,
        name: str = Form(""),
        code: str = Form(""),
        spend_cap_naira: str = Form(""),
    ) -> Response:
        account = await current_account(request)
        if account is None:
            return RedirectResponse("/login", status_code=303)
        if account.totp_secret is None:
            return RedirectResponse("/app/2fa", status_code=303)

        keys = await list_keys(key_store, account.id)
        if not step_up_ok(account, code):
            return keys_view(
                request, account, keys, error="That code did not match.", status_code=422
            )

        try:
            cap = _parse_cap(spend_cap_naira)
        except (ValueError, ArithmeticError):
            return keys_view(
                request,
                account,
                keys,
                error="Enter the cap in naira, for example 5000.",
                status_code=422,
            )

        try:
            key = await issue_key(
                request=NewKeyRequest(account_id=account.id, name=name, spend_cap_micro=cap),
                store=key_store,
                pepper=settings.key_pepper,
                product=settings.key_prefix,
                environment=settings.key_environment,
                new_id=uuid.uuid4().hex,
            )
        except KeyIssuanceRefusedError as error:
            return keys_view(request, account, keys, error=str(error), status_code=422)

        return render(request, "key_created.html", raw=key.raw, prefix=key.prefix)

    @router.post("/app/keys/revoke", dependencies=[Depends(require_csrf)])
    async def revoke(request: Request, key_id: str = Form(""), code: str = Form("")) -> Response:
        account = await current_account(request)
        if account is None:
            return RedirectResponse("/login", status_code=303)

        record = await key_store.by_id(key_id)
        if record is None or record.account_id != account.id:
            raise HTTPException(status_code=404, detail="no such key")

        keys = await list_keys(key_store, account.id)
        if not step_up_ok(account, code):
            return keys_view(
                request, account, keys, error="That code did not match.", status_code=422
            )

        await revoke_key(
            prefix=record.prefix, key_id=record.id, store=key_store, cache=key_cache, clock=clock
        )
        return RedirectResponse("/app/keys", status_code=303)

    return router


def _parse_cap(value: str) -> int | None:
    """Parse a naira amount into micro-naira. Empty means no cap."""
    text = value.strip().replace(",", "")
    if not text:
        return None
    return Naira.from_naira(text).micro


async def _second_factor_ok(
    store: AuthStore, account: AccountRecord, code: str, now: datetime, pepper: str
) -> bool:
    if not account.totp_secret:
        return True
    if verify(account.totp_secret, code, now):
        return True
    submitted = code.strip()
    if submitted:
        return await store.consume_recovery_code(account.id, hash_secret(submitted, pepper))
    return False


__all__ = ["build_router"]
