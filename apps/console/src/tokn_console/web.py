"""Request-level security: headers, session loading, CSRF.

Every response carries the full header set. Sessions are server-side; the browser holds a
random id and the database holds its keyed digest. CSRF uses the double-submit pattern
with a ``__Host-`` cookie, which no subdomain can overwrite.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from urllib.parse import urlsplit

from fastapi import HTTPException, Request, Response, status
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.types import ASGIApp

from tokn_core.auth.hashing import digests_match, hash_secret, new_secret
from tokn_core.auth.ports import Clock
from tokn_core.auth.sessions import Session, SessionExpiredError
from tokn_core.auth.store import AuthStore, SessionRecord

CSRF_COOKIE = "__Host-csrf"
CSRF_FIELD = "csrf_token"

CONTENT_SECURITY_POLICY = (
    "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; font-src 'self'; "
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


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Every response carries an id the customer can quote to support."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers.setdefault("x-request-id", request_id)
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, *, hsts: bool, exempt_prefixes: tuple[str, ...] = ()) -> None:
        super().__init__(app)
        self._hsts = hsts
        self._exempt = exempt_prefixes

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        path = request.url.path
        if any(path.startswith(prefix) for prefix in self._exempt):
            return response
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        nonce = getattr(request.state, "csp_nonce", None)
        if nonce:
            # The mirrored design is a static build whose own runtime injects inline
            # bootstrap scripts. It carries no user input, so its page policy allows
            # inline script while the console and API keep the strict policy above.
            response.headers["Content-Security-Policy"] = (
                "default-src 'none'; "
                "script-src 'self' 'unsafe-inline'; "
                "style-src 'self' 'unsafe-inline'; "
                "font-src 'self'; img-src 'self' data:; connect-src 'self'; "
                "form-action 'self'; frame-ancestors 'none'; base-uri 'none'; "
                "object-src 'none'"
            )
        if self._hsts:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=63072000; includeSubDomains; preload"
            )
        return response


class SessionMiddleware(BaseHTTPMiddleware):
    """Load the session for this request, or leave the request anonymous."""

    def __init__(
        self, app: ASGIApp, *, store: AuthStore, pepper: str, clock: Clock, cookie_name: str
    ) -> None:
        super().__init__(app)
        self._store = store
        self._pepper = pepper
        self._clock = clock
        self._cookie_name = cookie_name

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request.state.session = None
        request.state.session_digest = None
        path = request.url.path
        if path.startswith(("/_next", "/static", "/avatars", "/brand", "/images", "/fonts")):
            # An asset request should not touch a session row. Browsers send cookies for
            # every same-origin image and script; writing last_seen_at each time would be
            # a database write per byte served.
            return await call_next(request)
        raw = request.cookies.get(self._cookie_name)
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


def ensure_csrf_token(
    request: Request, response: Response, *, cookie_name: str, secure: bool
) -> str:
    """Return this browser's CSRF token, minting one into a cookie when absent."""
    existing = request.cookies.get(cookie_name)
    if existing:
        return existing
    token = new_secret(32)
    response.set_cookie(cookie_name, token, httponly=True, secure=secure, samesite="lax", path="/")
    return token


def csrf_matches(cookie: str | None, submitted: str | None) -> bool:
    if not cookie or not submitted:
        return False
    return digests_match(cookie, submitted)


async def submitted_csrf(request: Request, *, cookie_name: str) -> bool:
    """True when a form or JSON body carries the token from this browser's cookie."""
    cookie = request.cookies.get(cookie_name)
    if not cookie:
        return False
    if not same_origin(request):
        return False
    submitted = request.headers.get("x-csrf-token")
    if submitted is None:
        content_type = request.headers.get("content-type", "")
        if "application/json" in content_type:
            try:
                payload = await request.json()
            except ValueError:
                return False
            if isinstance(payload, dict):
                value = payload.get(CSRF_FIELD)
                submitted = value if isinstance(value, str) else None
        else:
            form = await request.form()
            value = form.get(CSRF_FIELD)
            submitted = value if isinstance(value, str) else None
    return csrf_matches(cookie, submitted)


def same_origin(request: Request) -> bool:
    """Refuse a browser request whose Origin is not the host we are serving.

    A missing Origin is allowed: command line clients and older browsers omit it, and the
    CSRF token still has to match. A present Origin must match the Host header exactly.
    """
    origin = request.headers.get("origin")
    if not origin:
        referer = request.headers.get("referer")
        if not referer:
            return True
        origin = f"{urlsplit(referer).scheme}://{urlsplit(referer).netloc}"
    host = request.headers.get("host", "")
    return urlsplit(origin).netloc == host and urlsplit(origin).scheme == request.url.scheme


async def require_csrf(request: Request) -> None:
    """Dependency for every non-GET route."""
    form = await request.form()
    submitted = form.get(CSRF_FIELD)
    cookie = request.cookies.get(CSRF_COOKIE)
    if not isinstance(submitted, str) or not csrf_matches(cookie, submitted):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="csrf")


def set_session_cookie(
    response: Response, raw: str, *, max_age: int, secure: bool, cookie_name: str
) -> None:
    response.set_cookie(
        cookie_name,
        raw,
        max_age=max_age,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response, *, secure: bool, cookie_name: str) -> None:
    response.delete_cookie(cookie_name, path="/", secure=secure, httponly=True, samesite="lax")


class SiteMiddleware(BaseHTTPMiddleware):
    """Load the operator's settings once per request so render can read them."""

    def __init__(self, app: ASGIApp, *, store: object) -> None:
        super().__init__(app)
        self._store = store

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request.state.settings = await self._store.load()  # type: ignore[attr-defined]
        return await call_next(request)
