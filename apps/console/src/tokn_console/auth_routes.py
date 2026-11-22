"""Sign in, sign up, sign out, reset.

The public design's own auth screens were static mirrors with no server behind them. These
routes replace them with the same product voice, in the site's theme, on one secure path:
Argon2id, per address and per account throttling, single-use tokens, and a server-side
session whose digest is all the database ever holds.
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request, Response, status
from fastapi.responses import JSONResponse

from tokn_console import accounts, ui
from tokn_console.responses import error_response, finish, redirect, token_for, wants_json
from tokn_console.services import Services
from tokn_console.web import client_ip, submitted_csrf
from tokn_core.auth.tokens import TokenKind, digest_for, issue_token
from tokn_core.site import SiteSettings


def _safe_next(value: str | None) -> str:
    if not value or not value.startswith("/") or value.startswith("//"):
        return "/dashboard"
    return value


def _field(
    name: str,
    label: str,
    *,
    kind: str = "text",
    value: str = "",
    autocomplete: str = "",
    placeholder: str = "",
    autofocus: bool = False,
) -> str:
    return (
        f'<div><label for="{name}">{ui.e(label)}</label>'
        f'<input id="{name}" name="{name}" type="{kind}" value="{ui.e(value)}" '
        f'autocomplete="{ui.e(autocomplete)}" placeholder="{ui.e(placeholder)}" '
        f'{"autofocus " if autofocus else ""}required></div>'
    )


def _login_html(
    settings: SiteSettings,
    *,
    csrf: str,
    email: str,
    next_path: str,
    error: str = "",
) -> str:
    notice = f'<div class="notice red"><p>{ui.e(error)}</p></div>' if error else ""
    body = f"""{notice}
<form class="stack" method="post" action="/login" data-busy>
{ui.csrf_field(csrf)}
<input type="hidden" name="next" value="{ui.e(next_path)}">
{_field("email", "Email", kind="email", value=email, autocomplete="email", placeholder="you@company.com", autofocus=True)}
{_field("password", "Password", kind="password", autocomplete="current-password", placeholder="your password")}
<button class="btn dark wide" type="submit" data-busy-label="Signing in">Sign in</button>
</form>"""
    foot = (
        'No account yet? <a href="/signup">Create one</a>. '
        '<a href="/forgot-password">Forgot your password?</a>'
    )
    return ui.auth_page(
        settings,
        title="Sign in",
        kicker="Welcome back",
        heading="Sign in",
        lead="Your credit is exactly where you left it.",
        body=body,
        aside_heading="One key. Every provider.",
        aside_body=(
            "The same wallet pays for Claude, GPT, Gemini and everything else the gateway "
            "routes. Credit never expires and every request has a receipt."
        ),
        aside_points=(
            ("Per token, in naira.", "No monthly clock counting down."),
            ("Receipts, not estimates.", "See model, tokens and cost for each call."),
            ("Your keys stay yours.", "Stored as a hash. Shown once, then never again."),
        ),
        foot=foot,
    )


def _signup_html(
    settings: SiteSettings,
    *,
    csrf: str,
    email: str,
    plan: str,
    error: str = "",
) -> str:
    notice = f'<div class="notice red"><p>{ui.e(error)}</p></div>' if error else ""
    plan_field = (
        f'<input type="hidden" name="plan" value="{ui.e(plan)}">' if plan else ""
    )
    body = f"""{notice}
<form class="stack" method="post" action="/signup" data-busy>
{ui.csrf_field(csrf)}
{plan_field}
{_field("email", "Email", kind="email", value=email, autocomplete="email", placeholder="you@company.com", autofocus=True)}
{_field("password", "Password", kind="password", autocomplete="new-password", placeholder="at least 12 characters")}
<div><label for="password-confirm">Confirm password</label>
<input id="password-confirm" name="password-confirm" type="password" autocomplete="new-password" required></div>
<p class="help">Twelve characters or more. A passphrase beats a clever short password.</p>
<button class="btn dark wide" type="submit" data-busy-label="Creating account">Create account</button>
</form>"""
    foot = 'Already have an account? <a href="/login">Sign in</a>.'
    return ui.auth_page(
        settings,
        title="Create account",
        kicker="Start here",
        heading="Create your account",
        lead="No card is stored. Top up when you are ready to send traffic.",
        body=body,
        aside_heading="Pay for what you send, nothing else.",
        aside_body=(
            "One prepaid naira wallet. Every request is reserved, settled and receipted. "
            "The balance you do not spend waits for you."
        ),
        aside_points=(
            ("No expiring monthly quota.", "Credit is credit. It does not reset."),
            ("OpenAI compatible.", "Point the client you already use at one base URL."),
            ("No prompt retention.", "Usage rows carry tokens and cost, never content."),
        ),
        foot=foot,
    )


def _reset_html(settings: SiteSettings, *, csrf: str, token: str, error: str = "") -> str:
    notice = f'<div class="notice red"><p>{ui.e(error)}</p></div>' if error else ""
    body = f"""{notice}
<form class="stack" method="post" action="/reset-password" data-busy>
{ui.csrf_field(csrf)}
<input type="hidden" name="token" value="{ui.e(token)}">
{_field("password", "New password", kind="password", autocomplete="new-password")}
<div><label for="password-confirm">Confirm new password</label>
<input id="password-confirm" name="password-confirm" type="password" autocomplete="new-password" required></div>
<button class="btn dark wide" type="submit" data-busy-label="Saving">Set new password</button>
</form>"""
    return ui.auth_page(
        settings,
        title="Reset password",
        kicker="One time link",
        heading="Set a new password",
        lead="This link works once and expires after thirty minutes.",
        body=body,
        aside_heading="If the link expired",
        aside_body="Ask for a new one from the sign in screen. Old sessions are ended when the password changes.",
        aside_points=(),
        foot='<a href="/login">Back to sign in</a>',
    )


def install(app: APIRouter, services: Services) -> None:
    settings = services.settings

    @app.get("/login", include_in_schema=False)
    async def login_form(
        request: Request, next_url: str = Query("", alias="next")
    ) -> Response:
        if await accounts.account_for(request, services):
            return redirect("/dashboard")
        token = token_for(request, services)
        html = _login_html(
            settings_snapshot(request), csrf=token, email="", next_path=_safe_next(next_url)
        )
        return finish(request, services, token, html)

    @app.post("/login", include_in_schema=False)
    async def login_submit(request: Request) -> Response:
        if not await submitted_csrf(request, cookie_name=services.csrf_cookie):
            return _csrf_failure(request, services)
        fields = await _fields(request)
        email = fields.get("email", "")
        password = fields.get("password", "")
        next_path = _safe_next(fields.get("next"))
        try:
            account = await accounts.authenticate(
                services, email=email, password=password, ip=client_ip(request)
            )
        except accounts.AccountError as error:
            if wants_json(request):
                return error_response(request, error.message, status_code=401)
            token = token_for(request, services)
            html = _login_html(
                settings_snapshot(request),
                csrf=token,
                email=email,
                next_path=next_path,
                error=error.message,
            )
            return finish(request, services, token, html, status_code=400)

        if wants_json(request):
            response: Response = JSONResponse(
                {"account": {"id": account.id, "email": account.email}}, status_code=200
            )
        else:
            response = redirect(next_path)
        await accounts.sign_in(request, response, services, account)
        return response

    @app.get("/signup", include_in_schema=False)
    async def signup_form(request: Request, plan: str = "") -> Response:
        if await accounts.account_for(request, services):
            return redirect("/dashboard")
        token = token_for(request, services)
        html = _signup_html(
            settings_snapshot(request), csrf=token, email="", plan=plan
        )
        return finish(request, services, token, html)

    @app.post("/signup", include_in_schema=False)
    async def signup_submit(request: Request) -> Response:
        if not await submitted_csrf(request, cookie_name=services.csrf_cookie):
            return _csrf_failure(request, services)
        fields = await _fields(request)
        email = fields.get("email", "")
        try:
            account = await accounts.register(
                services,
                email=email,
                password=fields.get("password", ""),
                confirmation=fields.get("password-confirm", ""),
                ip=client_ip(request),
            )
        except accounts.AccountError as error:
            if wants_json(request):
                return error_response(request, error.message, status_code=400)
            token = token_for(request, services)
            html = _signup_html(
                settings_snapshot(request),
                csrf=token,
                email=email,
                plan=fields.get("plan", ""),
                error=error.message,
            )
            return finish(request, services, token, html, status_code=400)

        if wants_json(request):
            response: Response = JSONResponse(
                {"account": {"id": account.id, "email": account.email}}, status_code=201
            )
        else:
            response = redirect("/dashboard/activate")
        await accounts.sign_in(request, response, services, account)
        return response

    @app.post("/api/auth/login")
    async def api_login(request: Request) -> Response:
        return await login_submit(request)

    @app.post("/api/auth/signup")
    async def api_signup(request: Request) -> Response:
        return await signup_submit(request)

    @app.post("/api/auth/logout")
    async def logout(request: Request) -> Response:
        if not await submitted_csrf(request, cookie_name=services.csrf_cookie):
            return _csrf_failure(request, services)
        response = JSONResponse({"status": "signed out"}) if wants_json(request) else redirect("/")
        await accounts.sign_out(request, response, services)
        return response

    @app.get("/api/auth/session")
    async def session_info(request: Request) -> Response:
        account = await accounts.account_for(request, services)
        if account is None:
            return JSONResponse({"account": None}, status_code=200)
        return JSONResponse(
            {
                "account": {
                    "id": account.id,
                    "email": account.email,
                    "email_verified": account.email_verified,
                    "is_admin": account.is_admin,
                }
            }
        )

    @app.get("/api/auth/csrf")
    async def csrf_token(request: Request) -> Response:
        """Mint a token for a JSON client. The cookie and the body carry the same value."""
        token = token_for(request, services)
        response = JSONResponse({"csrf_token": token})
        if request.cookies.get(services.csrf_cookie) != token:
            response.set_cookie(
                services.csrf_cookie,
                token,
                httponly=True,
                secure=services.secure_cookies,
                samesite="lax",
                path="/",
            )
        return response

    @app.get("/logout", include_in_schema=False)
    async def logout_page() -> Response:
        return redirect("/")

    @app.get("/forgot-password", include_in_schema=False)
    async def forgot_form(request: Request, sent: str = "") -> Response:
        token = token_for(request, services)
        notice = (
            '<div class="notice green"><p>If that address has an account, a reset link is '
            "on its way.</p></div>"
            if sent
            else ""
        )
        body = f"""{notice}
<form class="stack" method="post" action="/forgot-password" data-busy>
{ui.csrf_field(token)}
{_field("email", "Email", kind="email", autocomplete="email", autofocus=True)}
<button class="btn dark wide" type="submit">Send reset link</button>
</form>"""
        html = ui.auth_page(
            settings_snapshot(request),
            title="Forgot password",
            kicker="Account recovery",
            heading="Get back in",
            lead="We will queue a single use link. It expires in thirty minutes.",
            body=body,
            aside_heading="No link yet?",
            aside_body=(
                "Mail is queued through the outbox. If nothing arrives, write to support and "
                "a person will help."
            ),
            aside_points=(),
            foot=f'<a href="/login">Back to sign in</a> · {ui.e(settings.support_email)}',
        )
        return finish(request, services, token, html)

    @app.post("/forgot-password", include_in_schema=False)
    async def forgot_submit(request: Request) -> Response:
        if not await submitted_csrf(request, cookie_name=services.csrf_cookie):
            return _csrf_failure(request, services)
        fields = await _fields(request)
        email = fields.get("email", "").strip().lower()
        if email:
            account = await services.auth.account_by_email(email)
            if account is not None:
                issued = issue_token(
                    TokenKind.PASSWORD_RESET, services.clock.now(), settings.session_secret
                )
                await services.auth.issue_token(
                    account.id, issued.digest, issued.kind, issued.expires_at
                )
                link = f"{str(request.base_url).rstrip('/')}/reset-password?token={issued.raw}"
                await services.auth.enqueue_email(
                    account.email,
                    "Reset your password",
                    f"Open this link within thirty minutes, once: {link}",
                )
        if wants_json(request):
            return JSONResponse({"status": "queued"}, status_code=202)
        return redirect("/forgot-password?sent=1")

    @app.get("/reset-password", include_in_schema=False)
    async def reset_form(request: Request, token: str = "") -> Response:
        token = token or request.query_params.get("token", "")
        if not token:
            return redirect("/forgot-password")
        csrf = token_for(request, services)
        html = _reset_html(settings_snapshot(request), csrf=csrf, token=token)
        return finish(request, services, csrf, html)

    @app.post("/reset-password", include_in_schema=False)
    async def reset_submit(request: Request) -> Response:
        if not await submitted_csrf(request, cookie_name=services.csrf_cookie):
            return _csrf_failure(request, services)
        fields = await _fields(request)
        raw = fields.get("token", "")
        password = fields.get("password", "")
        confirmation = fields.get("password-confirm", "")
        digest = digest_for(raw, settings.session_secret)
        account_id = await services.auth.consume_token(
            digest, TokenKind.PASSWORD_RESET, services.clock.now()
        )
        if account_id is None:
            token = token_for(request, services)
            html = _reset_html(
                settings_snapshot(request),
                csrf=token,
                token=raw,
                error="That link is used or expired. Ask for a new one.",
            )
            return finish(request, services, token, html, status_code=400)
        account = await services.auth.account_by_id(account_id)
        if account is None:
            return redirect("/login")
        if password != confirmation:
            token = token_for(request, services)
            html = _reset_html(
                settings_snapshot(request),
                csrf=token,
                token=raw,
                error="The two passwords do not match.",
            )
            return finish(request, services, token, html, status_code=400)
        try:
            from tokn_core.auth.passwords import validate_password

            validate_password(password, context=account.email)
        except Exception as error:
            token = token_for(request, services)
            html = _reset_html(
                settings_snapshot(request), csrf=token, token=raw, error=str(error)
            )
            return finish(request, services, token, html, status_code=400)
        await services.auth.set_password_hash(account.id, services.hasher.hash(password))
        await services.auth.delete_sessions(account.id)
        return redirect("/login?reset=1")


def settings_snapshot(request: Request) -> SiteSettings:
    settings: SiteSettings = request.state.settings
    return settings


async def _fields(request: Request) -> dict[str, str]:
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            payload = await request.json()
        except ValueError:
            return {}
        if not isinstance(payload, dict):
            return {}
        return {str(key): str(value) for key, value in payload.items() if value is not None}
    form = await request.form()
    return {key: str(value) for key, value in form.items()}


def _csrf_failure(request: Request, services: Services) -> Response:
    message = "This form expired. Reload the page and try again."
    if wants_json(request):
        return error_response(request, message, status_code=status.HTTP_403_FORBIDDEN)
    token = token_for(request, services)
    html = ui.page(
        settings_snapshot(request),
        title="Form expired",
        description="Reload and try again.",
        path="/login",
        csrf=token,
        account=None,
        body=(
            f'<div class="card"><h1>That form expired</h1><p class="muted">{ui.e(message)}</p>'
            '<a class="btn" href="/login">Reload sign in</a></div>'
        ),
    )
    return finish(request, services, token, html, status_code=403)
