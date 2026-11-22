"""Small response helpers shared by every HTML route.

The CSRF token has to exist before the HTML is built and has to be in the cookie by the
time the browser reads it, so the route asks for the token first and finishes the response
second.
"""

from __future__ import annotations

import html

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from tokn_console.services import Services
from tokn_core.auth.hashing import new_secret


def token_for(request: Request, services: Services) -> str:
    return request.cookies.get(services.csrf_cookie) or new_secret(32)


def finish(
    request: Request,
    services: Services,
    token: str,
    html: str,
    *,
    status_code: int = 200,
) -> HTMLResponse:
    response = HTMLResponse(html, status_code=status_code)
    if request.cookies.get(services.csrf_cookie) != token:
        response.set_cookie(
            services.csrf_cookie,
            token,
            httponly=True,
            secure=services.secure_cookies,
            samesite="lax",
            path="/",
        )
    response.headers.setdefault("cache-control", "no-store")
    return response


def redirect(location: str, *, status_code: int = 303) -> RedirectResponse:
    return RedirectResponse(location, status_code=status_code)


def wants_json(request: Request) -> bool:
    if "application/json" in request.headers.get("content-type", ""):
        return True
    return request.headers.get("accept", "").startswith("application/json")


def error_response(request: Request, message: str, *, status_code: int = 400) -> Response:
    if wants_json(request):
        return JSONResponse({"error": message}, status_code=status_code)
    return HTMLResponse(
        f"<!doctype html><title>Error</title><p>{html.escape(message)}</p>",
        status_code=status_code,
    )
