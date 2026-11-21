"""Shared dependencies for the console routers.

Every router receives one ``Console`` object rather than a dozen keyword arguments. The
helpers here are the only places that mint sessions, render pages or set cookies, so the
rules stay in one file instead of being repeated per route.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from fastapi import Request, Response
from fastapi.responses import RedirectResponse
from starlette.templating import Jinja2Templates

from tokn_console.config import Settings
from tokn_console.static import static_url
from tokn_console.web import (
    CSRF_COOKIE,
    client_ip,
    set_session_cookie,
)
from tokn_core.auth.hashing import hash_secret, new_secret
from tokn_core.auth.ports import Clock, PasswordHasher
from tokn_core.auth.sessions import IDLE_TIMEOUT, Session, new_session
from tokn_core.auth.store import AccountRecord, AuthStore
from tokn_core.auth.throttle import ThrottleState
from tokn_core.key_auth import KeyCache, KeyStore


@dataclass(slots=True)
class Console:
    """Everything a route needs, assembled once at startup."""

    store: AuthStore
    key_store: KeyStore
    key_cache: KeyCache
    hasher: PasswordHasher
    clock: Clock
    settings: Settings
    templates: Jinja2Templates
    _static_versions: dict[str, str] = field(default_factory=dict)

    # -- rendering --------------------------------------------------------

    def render(
        self,
        request: Request,
        template: str,
        *,
        status_code: int = 200,
        **context: object,
    ) -> Response:
        token = request.cookies.get(CSRF_COOKIE) or new_secret(32)
        flash = getattr(request.state, "flash", None)
        body: dict[str, object] = {
            **context,
            "csrf_token": token,
            "product": self.settings.product_name,
            "support_email": self.settings.support_email,
            "flash": flash,
            "static_url": static_url,
            "domain": self.settings.domain,
            "cli_available": self.settings.cli_available,
            "session": getattr(request.state, "session", None),
        }
        response = self.templates.TemplateResponse(request, template, body, status_code=status_code)
        if request.cookies.get(CSRF_COOKIE) is None:
            response.set_cookie(
                CSRF_COOKIE, token, httponly=True, secure=True, samesite="lax", path="/"
            )
        # Console HTML is never cached: it carries a CSRF token and, when signed in,
        # another account's data is one back-button away.
        response.headers["cache-control"] = "no-store"
        return response

    def redirect(self, to: str) -> RedirectResponse:
        return RedirectResponse(to, status_code=303)

    # -- the signed-in user -----------------------------------------------

    def session(self, request: Request) -> Session | None:
        value: Session | None = getattr(request.state, "session", None)
        return value

    async def current_account(self, request: Request) -> AccountRecord | None:
        session = self.session(request)
        if session is None:
            return None
        return await self.store.account_by_id(session.account_id)

    async def start_session(self, request: Request, response: Response, account_id: str) -> None:
        """Always a fresh id: a session fixed before login is useless after it."""
        now: datetime = self.clock.now()
        ip = client_ip(request)
        user_agent = request.headers.get("user-agent")
        raw, _ = new_session(account_id, now, ip, user_agent)
        await self.store.create_session(
            hash_secret(raw, self.settings.session_secret), account_id, now, ip, user_agent
        )
        previous = request.cookies.get("__Host-session")
        if previous:
            await self.store.delete_session(hash_secret(previous, self.settings.session_secret))
        set_session_cookie(
            response,
            raw,
            max_age=int(IDLE_TIMEOUT.total_seconds()),
            secure=self.settings.secure_cookies,
        )

    # -- shared error shapes ----------------------------------------------

    def locked_response(self, request: Request, state: ThrottleState) -> Response:
        wait = state.retry_after(self.clock.now())
        return self.render(
            request,
            "layouts/message.html",
            status_code=429,
            heading="Too many attempts",
            body=f"Try again in {max(1, int(wait.total_seconds()))} seconds.",
        )

    async def record_failure(self, email: str, ip: str) -> None:
        now = self.clock.now()
        for identity in (f"email:{email}", f"ip:{ip}"):
            state = await self.store.throttle(identity)
            await self.store.save_throttle(identity, state.with_failure(now))
