"""Signup, verification, login, reset and two-factor.

Split out of the single routes module in UI-1. Behaviour is unchanged; every dependency
is reached through ``console`` rather than through a closure.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.responses import RedirectResponse

from tokn_console.web import clear_session_cookie, client_ip, require_csrf
from tokn_core.auth.hashing import hash_secret
from tokn_core.auth.passwords import WeakPasswordError, validate_password
from tokn_core.auth.store import AccountRecord
from tokn_core.auth.throttle import ThrottleState, is_over_limit
from tokn_core.auth.tokens import TokenKind, issue_token, new_recovery_codes
from tokn_core.auth.totp import generate_secret, provisioning_uri, verify
from tokn_core.errors import AccountExistsError

if TYPE_CHECKING:
    from tokn_console.context import Console
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
DUMMY_HASH = "$argon2id$v=19$m=19456,t=2,p=1$c29tZXNhbHRzYWx0$" + "0" * 43
RESET_LIMIT = 5
GENERIC_SIGNUP = "If that address can be used, a verification link is on its way."
GENERIC_RESET = "If that address has an account, a reset link is on its way."


def build_router(console: Console) -> APIRouter:
    router = APIRouter()

    # ---------------------------------------------------------------- signup

    @router.get("/signup")
    async def signup_form(request: Request) -> Response:
        return console.render(request, "signup.html")

    @router.post("/signup", dependencies=[Depends(require_csrf)])
    async def signup(
        request: Request,
        email: str = Form(""),
        password: str = Form(""),
    ) -> Response:
        normalised = email.strip().lower()
        if not EMAIL_RE.match(normalised):
            return console.render(
                request, "signup.html", status_code=422, error="Use a valid email address."
            )
        try:
            validate_password(password, context=normalised)
        except WeakPasswordError as error:
            return console.render(request, "signup.html", status_code=422, error=str(error))

        try:
            account = await console.store.create_account(normalised, console.hasher.hash(password))
        except AccountExistsError:
            return console.render(
                request, "layouts/message.html", heading="Check your email", body=GENERIC_SIGNUP
            )

        token = issue_token(
            TokenKind.EMAIL_VERIFICATION, console.clock.now(), console.settings.session_secret
        )
        await console.store.issue_token(account.id, token.digest, token.kind, token.expires_at)
        link = f"{console.settings.console_url}/verify?token={token.raw}"
        await console.store.enqueue_email(
            normalised,
            "Verify your email",
            f"Open this link within 30 minutes: {link}",
        )
        return console.render(
            request, "layouts/message.html", heading="Check your email", body=GENERIC_SIGNUP
        )

    @router.get("/verify")
    async def verify_email(request: Request, token: str = "") -> Response:
        digest = hash_secret(token, console.settings.session_secret) if token else ""
        account_id = await console.store.consume_token(
            digest, TokenKind.EMAIL_VERIFICATION, console.clock.now()
        )
        if account_id is None:
            return console.render(
                request,
                "layouts/message.html",
                status_code=400,
                heading="Link not valid",
                body="This link has expired or has already been used. Request a new one.",
            )
        await console.store.set_email_verified(account_id, console.clock.now())
        return console.render(
            request,
            "layouts/message.html",
            heading="Email verified",
            body="You can sign in now.",
        )

    # ----------------------------------------------------------------- login

    @router.get("/login")
    async def login_form(request: Request) -> Response:
        return console.render(request, "login.html")

    @router.post("/login", dependencies=[Depends(require_csrf)])
    async def login(
        request: Request,
        email: str = Form(""),
        password: str = Form(""),
        code: str = Form(""),
    ) -> Response:
        normalised = email.strip().lower()
        ip = client_ip(request)
        email_state = await console.store.throttle(f"email:{normalised}")
        ip_state = await console.store.throttle(f"ip:{ip}")
        if email_state.is_locked(console.clock.now()) or ip_state.is_locked(console.clock.now()):
            return console.locked_response(
                request, max([email_state, ip_state], key=lambda s: s.failures)
            )

        account = await console.store.account_by_email(normalised)
        if account is None or account.password_hash is None:
            console.hasher.verify(password, DUMMY_HASH)  # equalise timing
            await console.record_failure(normalised, ip)
            return console.render(
                request, "login.html", status_code=401, error="Email or password is incorrect."
            )

        if not console.hasher.verify(password, account.password_hash):
            await console.record_failure(normalised, ip)
            return console.render(
                request, "login.html", status_code=401, error="Email or password is incorrect."
            )

        if not await _second_factor_ok(console, account, code):
            await console.record_failure(normalised, ip)
            return console.render(
                request,
                "login.html",
                status_code=401,
                error="Email, password or code is incorrect.",
                needs_code=True,
            )

        if not account.can_log_in:
            return console.render(
                request,
                "layouts/message.html",
                status_code=403,
                heading="Account not active",
                body="Verify your email address before signing in.",
            )

        for identity in (f"email:{normalised}", f"ip:{ip}"):
            await console.store.save_throttle(identity, ThrottleState())

        response = RedirectResponse("/app", status_code=303)
        await console.start_session(request, response, account.id)
        return response

    @router.post("/logout", dependencies=[Depends(require_csrf)])
    async def logout(request: Request) -> Response:
        raw = request.cookies.get("__Host-session")
        if raw:
            await console.store.delete_session(hash_secret(raw, console.settings.session_secret))
        response = RedirectResponse("/login", status_code=303)
        clear_session_cookie(response, secure=console.settings.secure_cookies)
        return response

    # ----------------------------------------------------------------- reset

    @router.get("/reset")
    async def reset_form(request: Request) -> Response:
        return console.render(request, "reset.html")

    @router.post("/reset", dependencies=[Depends(require_csrf)])
    async def reset_request(request: Request, email: str = Form("")) -> Response:
        normalised = email.strip().lower()
        ip = client_ip(request)
        state = await console.store.throttle(f"ip:{ip}:reset")
        if is_over_limit(state, console.clock.now(), limit=RESET_LIMIT):
            return console.locked_response(request, state)
        await console.store.save_throttle(f"ip:{ip}:reset", state.with_failure(console.clock.now()))

        account = (
            await console.store.account_by_email(normalised) if EMAIL_RE.match(normalised) else None
        )
        if account is not None:
            token = issue_token(
                TokenKind.PASSWORD_RESET, console.clock.now(), console.settings.session_secret
            )
            await console.store.issue_token(account.id, token.digest, token.kind, token.expires_at)
            link = f"{console.settings.console_url}/reset/confirm?token={token.raw}"
            await console.store.enqueue_email(
                normalised, "Reset your password", f"Open this link within 30 minutes: {link}"
            )
        return console.render(
            request, "layouts/message.html", heading="Check your email", body=GENERIC_RESET
        )

    @router.get("/reset/confirm")
    async def reset_confirm_form(request: Request, token: str = "") -> Response:
        return console.render(request, "reset_confirm.html", token=token)

    @router.post("/reset/confirm", dependencies=[Depends(require_csrf)])
    async def reset_confirm(
        request: Request, token: str = Form(""), password: str = Form("")
    ) -> Response:
        try:
            validate_password(password)
        except WeakPasswordError as error:
            return console.render(
                request, "reset_confirm.html", status_code=422, token=token, error=str(error)
            )
        digest = hash_secret(token, console.settings.session_secret) if token else ""
        account_id = await console.store.consume_token(
            digest, TokenKind.PASSWORD_RESET, console.clock.now()
        )
        if account_id is None:
            return console.render(
                request,
                "layouts/message.html",
                status_code=400,
                heading="Link not valid",
                body="This link has expired or has already been used.",
            )
        await console.store.set_password_hash(account_id, console.hasher.hash(password))
        await console.store.delete_sessions(account_id)
        return console.render(
            request,
            "layouts/message.html",
            heading="Password changed",
            body="Sign in with your new password.",
        )

    # ------------------------------------------------------------- two-factor

    @router.get("/app/2fa")
    async def two_factor_form(request: Request) -> Response:
        account = await console.current_account(request)
        if account is None:
            return RedirectResponse("/login", status_code=303)
        secret = account.totp_secret or generate_secret()
        return console.render(
            request,
            "two_factor.html",
            secret=secret,
            uri=provisioning_uri(secret, account.email, console.settings.product_name),
        )

    @router.post("/app/2fa", dependencies=[Depends(require_csrf)])
    async def two_factor_enable(
        request: Request, secret: str = Form(""), code: str = Form("")
    ) -> Response:
        account = await console.current_account(request)
        if account is None:
            return RedirectResponse("/login", status_code=303)
        if not verify(secret, code, console.clock.now()):
            return console.render(
                request,
                "two_factor.html",
                status_code=422,
                secret=secret,
                uri=provisioning_uri(secret, account.email, console.settings.product_name),
                error="That code did not match. Check your authenticator and try again.",
            )
        await console.store.set_totp_secret(account.id, secret)
        codes = new_recovery_codes(console.settings.session_secret)
        await console.store.set_recovery_codes(account.id, tuple(digest for _, digest in codes))
        return console.render(request, "recovery_codes.html", codes=[raw for raw, _ in codes])

    return router


async def _second_factor_ok(console: Console, account: AccountRecord, code: str) -> bool:
    """A TOTP code, or a recovery code that is then burned."""
    if not account.totp_secret:
        return True
    if verify(account.totp_secret, code, console.clock.now()):
        return True
    submitted = code.strip()
    if submitted:
        return await console.store.consume_recovery_code(
            account.id, hash_secret(submitted, console.settings.session_secret)
        )
    return False
