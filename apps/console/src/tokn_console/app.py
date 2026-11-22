"""The console application factory.

Dependencies are injected, so tests supply an in-memory store and a frozen clock while
production picks PostgreSQL the moment ``TOKN_DATABASE_URL`` is set.
"""

from __future__ import annotations

import pathlib
import re
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    RedirectResponse,
    Response,
)
from sqlalchemy.ext.asyncio import AsyncEngine
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from tokn_console.config import Settings
from tokn_console.context import Console, SiteSettingsStore
from tokn_console.routes import build_router
from tokn_console.static import HashedStaticFiles, static_url
from tokn_console.ui import FlashMiddleware
from tokn_console.web import (
    RequestIdMiddleware,
    SecurityHeadersMiddleware,
    SessionMiddleware,
    SiteMiddleware,
)
from tokn_core.auth.ports import Clock, PasswordHasher
from tokn_core.auth.store import AuthStore
from tokn_core.key_auth import KeyCache, KeyStore
from tokn_infra.clock import SystemClock
from tokn_infra.memory.auth_store import InMemoryAuthStore
from tokn_infra.memory.key_store import InMemoryKeyCache, InMemoryKeyStore
from tokn_infra.memory.throttle import InMemoryThrottleStore
from tokn_infra.postgres import (
    PostgresAuthStore,
    PostgresKeyStore,
    create_engine,
    create_session_factory,
)
from tokn_infra.security import Argon2PasswordHasher
from tokn_infra.site_store import InMemorySiteSettings, PostgresSiteSettings

PACKAGE_ROOT = Path(__file__).resolve().parent
TEMPLATES = PACKAGE_ROOT / "templates"
STATIC = PACKAGE_ROOT / "static"
REPO_ROOT = PACKAGE_ROOT.parents[3]
SITE = REPO_ROOT / "brewkeg.dev"

ERROR_COPY = {
    403: ("Not allowed", "This account cannot do that."),
    404: ("Page not found", "That page does not exist, or it has moved."),
    500: ("Something went wrong", "Nothing was charged. Try again in a moment."),
}


TRACKERS = (
    # Google Tag Manager, Meta pixel, their og image service, their search widget.
    (r'<link rel="preload" href="https://www\.googletagmanager\.com[^>]*>', ""),
    (r'<script[^>]*googletagmanager[^>]*></script>', ""),
    (r'<script[^>]*connect\.facebook\.net[^>]*></script>', ""),
    (r"<noscript><img height=\"1\" width=\"1\"[^>]*facebook\.com/tr[^>]*></noscript>", ""),
    (r'<link rel="preload" href="https://og\.brewkeg\.dev[^>]*>', ""),
    (r'<script>\(self\.__next_s=self\.__next_s\|\|\[\]\)\.push\(\[0,\{"children":"!function\(f,b,e,v,n,t,s\)[^<]*</script>', ""),
)


def _strip_trackers(html: str) -> str:
    """The mirror carries their analytics and ad pixel. Serving those would report our
    visitors into someone else's accounts and contradict the privacy page."""
    for pattern, replacement in TRACKERS:
        html = re.sub(pattern, replacement, html, flags=re.DOTALL)
    # the cookie banner only exists because of those pixels
    html = re.sub(r'<aside id="cookie-consent".*?</aside>', "", html, flags=re.DOTALL)
    return html


def _serve_html(path: pathlib.Path) -> HTMLResponse:
    """Serve a mirrored page with our theme attached and their trackers removed."""
    html = _strip_trackers(path.read_text(encoding="utf-8", errors="replace"))
    link = '<link rel="stylesheet" href="/static/css/site-extras.css">'
    if "</head>" in html:
        html = html.replace("</head>", f"{link}</head>", 1)
    return HTMLResponse(html)


def _mount_site(app: FastAPI) -> None:
    """Serve the design folder, including the clean URLs it links to."""
    if not SITE.is_dir():
        return

    @app.get("/", include_in_schema=False)
    async def site_index() -> Response:
        index = SITE / "index.html"
        if not index.is_file():
            raise HTTPException(status_code=404)
        return _serve_html(index)

    @app.get("/_next/image", include_in_schema=False)
    async def next_image(url: str = "") -> Response:
        """The mirror lost Next's optimiser, so point at the original asset.

        The query is percent encoded (``%2Fbrand%2Ficon.png``) and the mirror dropped
        some brand files, so fall back to the icon that did survive.
        """
        wanted = unquote(url).lstrip("/")
        if (SITE / wanted).is_file():
            return RedirectResponse(f"/{wanted}")
        for fallback in ("icon-192.png", "icon-512.png"):
            if (SITE / fallback).is_file():
                return RedirectResponse(f"/{fallback}")
        raise HTTPException(status_code=404)

    @app.get("/{page}", include_in_schema=False)
    async def flat_page(page: str) -> Response:
        candidate = SITE / f"{page}.html"
        if candidate.is_file():
            return _serve_html(candidate)
        asset = SITE / page
        if asset.is_file():
            return FileResponse(asset)
        raise HTTPException(status_code=404)

    app.mount("/", StaticFiles(directory=str(SITE), html=True), name="site")


def create_app(
    settings: Settings | None = None,
    *,
    store: AuthStore | None = None,
    key_store: KeyStore | None = None,
    key_cache: KeyCache | None = None,
    hasher: PasswordHasher | None = None,
    clock: Clock | None = None,
    site: SiteSettingsStore | None = None,
) -> FastAPI:
    """Build the console. Fails loudly rather than serving auth on placeholder secrets."""
    resolved = (settings or Settings()).validated()
    hasher = hasher or Argon2PasswordHasher()
    clock = clock or SystemClock()

    engine: AsyncEngine | None = None
    if store is None:
        if resolved.database_url:
            engine = create_engine(resolved.database_url, pool_size=resolved.database_pool_size)
            factory = create_session_factory(engine)
            store = PostgresAuthStore(factory, InMemoryThrottleStore())
            key_store = key_store or PostgresKeyStore(factory)
        elif resolved.env == "development":
            store = InMemoryAuthStore()
        else:
            raise RuntimeError("set TOKN_DATABASE_URL, or provide an AuthStore explicitly")

    site_store = site or (
        PostgresSiteSettings(factory) if engine is not None else InMemorySiteSettings()
    )

    key_store = key_store or InMemoryKeyStore()
    key_cache = key_cache or InMemoryKeyCache(clock)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            if engine is not None:
                await engine.dispose()

    templates = Jinja2Templates(directory=str(TEMPLATES))
    app = FastAPI(
        title="Tokn Console",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.add_middleware(FlashMiddleware, settings=resolved)
    app.add_middleware(SiteMiddleware, store=site_store)
    app.add_middleware(SessionMiddleware, store=store, pepper=resolved.session_secret, clock=clock)
    app.add_middleware(
        SecurityHeadersMiddleware,
        hsts=resolved.enforce_https,
        exempt_prefixes=("/_next", "/avatars", "/brand", "/images", "/fonts"),
    )
    app.add_middleware(RequestIdMiddleware)
    app.mount("/static", HashedStaticFiles(directory=str(STATIC)), name="static")
    console = Console(
        store=store,
        key_store=key_store,
        key_cache=key_cache,
        hasher=hasher,
        clock=clock,
        settings=resolved,
        templates=templates,
        site=site_store,
    )
    app.include_router(build_router(console))

    def error_page(request: Request, status: int, request_id: str) -> Response:
        heading, body = ERROR_COPY.get(status, ERROR_COPY[500])
        context: dict[str, Any] = {
            "product": resolved.product_name,
            "support_email": resolved.support_email,
            "static_url": static_url,
            "flash": None,
            "session": None,
            "heading": heading,
            "body": body,
            "request_id": request_id,
        }
        return templates.TemplateResponse(request, "errors/error.html", context, status_code=status)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> Response:
        request_id = getattr(request.state, "request_id", uuid.uuid4().hex)
        location = (exc.headers or {}).get("location")
        if 300 <= exc.status_code < 400 and location:
            return RedirectResponse(location, status_code=exc.status_code)
        if request.headers.get("hx-request") == "true":
            return Response(
                status_code=exc.status_code,
                headers={"x-request-id": request_id},
            )
        return error_page(request, exc.status_code, request_id)

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> Response:
        request_id = getattr(request.state, "request_id", uuid.uuid4().hex)
        if request.headers.get("hx-request") == "true":
            return Response(status_code=500, headers={"x-request-id": request_id})
        return error_page(request, 500, request_id)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    _mount_site(app)
    return app
