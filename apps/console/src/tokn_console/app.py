"""The console process, now API and site host only.

There is no server-rendered UI left. The public site is the design folder, served with
clean URLs. What remains of ours is the health check, the theme stylesheet the design
reads, and the JSON endpoints other services call.
"""

from __future__ import annotations

import pathlib
import re
from urllib.parse import unquote

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from starlette.staticfiles import StaticFiles

from tokn_console.config import Settings
from tokn_console.static import HashedStaticFiles
from tokn_console.web import (
    RequestIdMiddleware,
    SecurityHeadersMiddleware,
    SiteMiddleware,
)
from tokn_core.site import SiteSettings
from tokn_infra.site_store import InMemorySiteSettings, PostgresSiteSettings

PACKAGE_ROOT = pathlib.Path(__file__).resolve().parent
STATIC = PACKAGE_ROOT / "static"
REPO_ROOT = PACKAGE_ROOT.parents[3]
SITE = REPO_ROOT / "brewkeg.dev"

TRACKERS = (
    (r'<link rel="preload" href="https://www\.googletagmanager\.com[^>]*>', ""),
    (r"<script[^>]*googletagmanager[^>]*></script>", ""),
    (r"<script[^>]*connect\.facebook\.net[^>]*></script>", ""),
    (r"<noscript><img height=\"1\" width=\"1\"[^>]*facebook\.com/tr[^>]*></noscript>", ""),
    (r'<link rel="preload" href="https://og\.brewkeg\.dev[^>]*>', ""),
    (r'<aside id="cookie-consent".*?</aside>', ""),
)

MAINTENANCE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{name}</title><link rel="stylesheet" href="/theme.css">
<style>body{{font-family:system-ui,sans-serif;background:var(--paper);color:var(--ink);
display:grid;place-items:center;min-height:100vh;margin:0;padding:24px;text-align:center}}</style>
</head><body><main><h1>{name}</h1><p>{message}</p></main></body></html>"""


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = (settings or Settings()).validated()
    engine = None
    site_store: InMemorySiteSettings | PostgresSiteSettings
    if resolved.database_url:
        from tokn_infra.postgres import create_engine, create_session_factory

        engine = create_engine(resolved.database_url, pool_size=resolved.database_pool_size)
        site_store = PostgresSiteSettings(create_session_factory(engine))
    else:
        site_store = InMemorySiteSettings()

    app = FastAPI(
        title="Brewkeg",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.add_middleware(SiteMiddleware, store=site_store)
    app.add_middleware(
        SecurityHeadersMiddleware,
        hsts=resolved.enforce_https,
        exempt_prefixes=("/_next", "/avatars", "/brand", "/images", "/fonts"),
    )
    app.add_middleware(RequestIdMiddleware)
    app.mount("/static", HashedStaticFiles(directory=str(STATIC)), name="static")

    @app.get("/theme.css", include_in_schema=False)
    async def theme(request: Request) -> Response:
        settings_in_use: SiteSettings = request.state.settings
        return PlainTextResponse(
            settings_in_use.theme_css(),
            media_type="text/css",
            headers={"cache-control": "no-store"},
        )

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    _mount_site(app)
    return app


def _strip_trackers(html: str) -> str:
    """Their mirrors carry analytics and an ad pixel. Ours must not."""
    for pattern, replacement in TRACKERS:
        html = re.sub(pattern, replacement, html, flags=re.DOTALL)
    return html


def _serve_html(path: pathlib.Path) -> HTMLResponse:
    html = _strip_trackers(path.read_text(encoding="utf-8", errors="replace"))
    # clean URLs: every internal .html link becomes a path without the extension
    html = re.sub(r'href="(?!https?:|mailto:|#)([^"]+?)\.html(#[^"]*)?"', r'href="/\1\2"', html)
    link = '<link rel="stylesheet" href="/static/css/site-extras.css">'
    if "</head>" in html:
        html = html.replace("</head>", f"{link}</head>", 1)
    return HTMLResponse(html)


def _mount_site(app: FastAPI) -> None:
    if not SITE.is_dir():
        return

    @app.get("/", include_in_schema=False)
    async def site_index(request: Request) -> Response:
        settings: SiteSettings = request.state.settings
        if not settings.available:
            return HTMLResponse(
                MAINTENANCE.format(name=settings.name, message=settings.maintenance_message),
                status_code=503,
            )
        index = SITE / "index.html"
        if not index.is_file():
            raise HTTPException(status_code=404)
        return _serve_html(index)

    @app.get("/_next/image", include_in_schema=False)
    async def next_image(url: str = "") -> Response:
        wanted = unquote(url).lstrip("/")
        if (SITE / wanted).is_file():
            return RedirectResponse(f"/{wanted}")
        for fallback in ("icon-192.png", "icon-512.png"):
            if (SITE / fallback).is_file():
                return RedirectResponse(f"/{fallback}")
        raise HTTPException(status_code=404)

    @app.get("/{page}", include_in_schema=False)
    async def flat_page(request: Request, page: str) -> Response:
        if page.endswith(".html"):
            return RedirectResponse(f"/{page[: -len('.html')]}", status_code=301)

        settings: SiteSettings = request.state.settings
        candidate = SITE / f"{page}.html"
        if candidate.is_file():
            if not settings.available:
                return HTMLResponse(
                    MAINTENANCE.format(name=settings.name, message=settings.maintenance_message),
                    status_code=503,
                )
            return _serve_html(candidate)

        asset = SITE / page
        if asset.is_file():
            return RedirectResponse(f"/{page}")
        raise HTTPException(status_code=404)

    app.mount("/", StaticFiles(directory=str(SITE), html=True), name="site")
