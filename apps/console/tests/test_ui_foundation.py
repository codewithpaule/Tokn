"""UI-0: the foundation rules, expressed as tests.

If any of these fail, the security posture or the feedback contract has regressed.
"""

from __future__ import annotations

import re

from starlette.requests import Request
from support import Harness

from tokn_console.ui import FLASH_COOKIE, flash, read_flash, toast

INLINE_SCRIPT = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>", re.IGNORECASE)
INLINE_HANDLER = re.compile(r"<[^>]+\son[a-z]+\s*=", re.IGNORECASE)
STYLE_ATTRIBUTE = re.compile(r"<[^>]+\sstyle\s*=", re.IGNORECASE)


async def test_no_page_contains_inline_script_style_or_handlers(harness: Harness) -> None:
    for path in ("/signup", "/login", "/reset"):
        page = await harness.client.get(path)
        html = page.text
        assert not INLINE_SCRIPT.search(html), f"inline <script> on {path}"
        assert not INLINE_HANDLER.search(html), f"on*= handler on {path}"
        assert not STYLE_ATTRIBUTE.search(html), f"style= attribute on {path}"


async def test_every_stylesheet_and_script_url_carries_a_hash(harness: Harness) -> None:
    html = (await harness.client.get("/login")).text

    assert re.search(r'href="/static/css/tokens\.css\?v=[0-9a-f]{12}"', html)
    assert re.search(r'href="/static/css/components\.css\?v=[0-9a-f]{12}"', html)
    assert re.search(r'src="/static/js/ui\.js\?v=[0-9a-f]{12}"', html)


async def test_vendored_scripts_are_pinned_with_sri(harness: Harness) -> None:
    html = (await harness.client.get("/login")).text

    assert 'integrity="sha384-' in html
    assert "htmx.min.js" in html
    assert "alpine-csp.min.js" in html
    assert 'crossorigin="anonymous"' in html


async def test_static_files_are_immutable(harness: Harness) -> None:
    response = await harness.client.get("/static/css/base.css")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"


async def test_html_is_never_cached(harness: Harness) -> None:
    response = await harness.client.get("/login")
    assert response.headers["cache-control"] == "no-store"


async def test_every_response_carries_a_request_id(harness: Harness) -> None:
    response = await harness.client.get("/login")
    assert len(response.headers["x-request-id"]) == 32


async def test_an_unknown_page_is_a_calm_404_with_a_request_id(harness: Harness) -> None:
    response = await harness.client.get("/nope")

    assert response.status_code == 404
    assert "Page not found" in response.text
    assert response.headers["x-request-id"] in response.text


def test_toast_sets_the_htmx_trigger_header() -> None:
    from starlette.responses import Response

    response = Response()
    toast(response, "success", "Key created")

    assert (
        response.headers["hx-trigger"] == '{"toast": {"kind": "success", "message": "Key created"}}'
    )


def test_toast_rejects_an_unknown_kind() -> None:
    from starlette.responses import Response

    try:
        toast(Response(), "shout", "x")
    except ValueError:
        return
    raise AssertionError("an unknown toast kind must be refused")


def test_flash_round_trips_and_is_signed() -> None:
    from starlette.responses import Response

    signing_key = "test-flash-secret-at-least-16"

    response = Response()
    flash(response, _settings(signing_key), "warning", "Low balance")
    cookie = response.headers["set-cookie"]
    assert cookie.startswith(f"{FLASH_COOKIE}=")
    assert "HttpOnly" in cookie

    value = cookie.split(";", 1)[0]
    request = _request(value)
    assert read_flash(request, signing_key) == {"kind": "warning", "message": "Low balance"}
    assert read_flash(request, "a-different-secret") is None, "a forged flash is ignored"


def _settings(secret: str):
    from tokn_console.config import Settings

    return Settings(env="development", session_secret=secret)


def _request(cookie_pair: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(b"cookie", cookie_pair.encode())],
        }
    )
