"""Server-driven toasts.

Two paths, because a response is either an htmx fragment or a full page:

* ``toast()`` sets an ``HX-Trigger`` header, so htmx fires the event and the client shows
  it without a reload.
* ``flash()`` stores a signed, short-lived cookie that the next page reads once, so a
  redirect after a normal form post still says something.

The message is data, never markup: the client builds toasts with DOM methods and
``textContent``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.types import ASGIApp

from tokn_console.config import Settings

FLASH_COOKIE = "__Host-flash"
KINDS = frozenset({"success", "info", "warning", "error"})
MAX_MESSAGE = 300


def toast(response: Response, kind: str, message: str) -> None:
    """Ask the client to show a toast. Safe on any response."""
    if kind not in KINDS:
        raise ValueError(f"unknown toast kind: {kind}")
    response.headers["HX-Trigger"] = json.dumps(
        {"toast": {"kind": kind, "message": message[:MAX_MESSAGE]}}
    )


def _sign(payload: str, secret: str) -> str:
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()[:32]


def flash(response: Response, settings: Settings, kind: str, message: str) -> None:
    """Queue a message for the next page. Signed so a user cannot forge one."""
    if kind not in KINDS:
        raise ValueError(f"unknown toast kind: {kind}")
    payload = json.dumps({"kind": kind, "message": message[:MAX_MESSAGE]}, separators=(",", ":"))
    token = base64.urlsafe_b64encode(payload.encode()).decode()
    response.set_cookie(
        FLASH_COOKIE,
        f"{token}.{_sign(token, settings.session_secret)}",
        max_age=60,
        httponly=True,
        secure=settings.secure_cookies,
        samesite="lax",
        path="/",
    )


def read_flash(request: Request, secret: str) -> dict[str, str] | None:
    """Return the queued message if it verifies, else None. Never raises."""
    raw = request.cookies.get(FLASH_COOKIE)
    if not raw or "." not in raw:
        return None
    token, _, signature = raw.rpartition(".")
    if not hmac.compare_digest(signature, _sign(token, secret)):
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(token.encode()))
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    kind = payload.get("kind")
    message = payload.get("message")
    if kind not in KINDS or not isinstance(message, str):
        return None
    return {"kind": kind, "message": message}


class FlashMiddleware(BaseHTTPMiddleware):
    """Read the flash cookie once per request and clear it on the way out."""

    def __init__(self, app: ASGIApp, *, settings: Settings) -> None:
        super().__init__(app)
        self._settings = settings

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request.state.flash = read_flash(request, self._settings.session_secret)
        response = await call_next(request)
        if request.state.flash is not None:
            response.delete_cookie(
                FLASH_COOKIE, path="/", secure=self._settings.secure_cookies, httponly=True
            )
        return response
