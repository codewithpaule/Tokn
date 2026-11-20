"""Request-level security: headers, session loading, CSRF.

Every response carries the full header set. Sessions are server-side; the browser holds a
random id and the database holds its keyed digest. CSRF uses the double-submit pattern
with a ``__Host-`` cookie, which no subdomain can overwrite.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import HTTPException, Request, Response, status
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.types import ASGIApp

from tokn_core.auth.hashing import digests_match, hash_secret, new_secret
from tokn_core.auth.ports import Clock
from tokn_core.auth.sessions import COOKIE_NAME, Session, SessionExpiredError
from tokn_core.auth.store import AuthStore, SessionRecord

CSRF_COOKIE = "__Host-csrf"
CSRF_FIELD = "csrf_token"

CONTENT_SECURITY_POLICY = (
    "default-src 'none'; script-src 'self'; style-src 'self'; font-src 'self'; "
    "img-src 'self' data:; connect-src 'self'; form-action 'self'; "
    "frame-ancestors 'none'; base-uri 'none'; object-src 'none'"
)

SECURITY_HEADERS = {
    "Content-Security-Policy": CONTENT_SECURITY_POLICY,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "X-Frame-Options": "DENY",
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, *, hsts: bool) -> None:
        super().__init__(app)
        self._hsts = hsts

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        if self._hsts:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=63072000; includeSubDomains; preload"
            )
        return response


class SessionMiddleware(BaseHTTPMiddleware):
    """Load the session for this request, or leave the request anonymous."""

    def __init__(self, app: ASGIApp, *, store: AuthStore, pepper: str, clock: Clock) -> None:
        super().__init__(app)
        self._store = store
        self._pepper = pepper
        self._clock = clock

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request.state.session = None
        request.state.session_digest = None
        raw = request.cookies.get(COOKIE_NAME)
        if raw:
            await self._load(request, raw)
        return await call_next(request)

    async def _load(self, request: Request, raw: str) -> None:
        digest = hash_secret(raw, self._pepper)
        record: SessionRecord | None = await self._store.session(digest)
        if record is None:
            return
        session = Session(
            account_id=record.account_id,
            created_at=record.created_at,
            last_seen_at=record.last_seen_at,
        )
        now: datetime = self._clock.now()
        try:
            touched = session.touch(now)
        except SessionExpiredError:
            await self._store.delete_session(digest)
            return
        await self._store.touch_session(digest, now)
        request.state.session = touched
        request.state.session_digest = digest


def client_ip(request: Request) -> str:
    """The address we throttle on. Behind Fly the proxy sets fly-client-ip."""
    forwarded = request.headers.get("fly-client-ip") or request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def ensure_csrf_token(request: Request, response: Response) -> str:
    """Return this browser's CSRF token, minting one into a cookie when absent."""
    existing = request.cookies.get(CSRF_COOKIE)
    if existing:
        return existing
    token = new_secret(32)
    response.set_cookie(CSRF_COOKIE, token, httponly=True, secure=True, samesite="lax", path="/")
    return token


def csrf_matches(cookie: str | None, submitted: str | None) -> bool:
    if not cookie or not submitted:
        return False
    return digests_match(cookie, submitted)


async def require_csrf(request: Request) -> None:
    """Dependency for every non-GET route."""
    form = await request.form()
    submitted = form.get(CSRF_FIELD)
    cookie = request.cookies.get(CSRF_COOKIE)
    if not isinstance(submitted, str) or not csrf_matches(cookie, submitted):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="csrf")


def set_session_cookie(response: Response, raw: str, *, max_age: int, secure: bool) -> None:
    response.set_cookie(
        COOKIE_NAME,
        raw,
        max_age=max_age,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response, *, secure: bool) -> None:
    response.delete_cookie(COOKIE_NAME, path="/", secure=secure, httponly=True, samesite="lax")
