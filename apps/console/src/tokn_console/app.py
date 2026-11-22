"""The console process: JSON API, customer console, and the public site host.

There are three surfaces, in this order:

1. ``/api`` and the signed-in screens, rendered by us with one stylesheet and a strict
   script policy.
2. ``/theme.css`` and ``/static``, the operator's tokens and our assets.
3. The design folder, served with clean URLs. Its own React build is allowed to hydrate
   with a per-response nonce so menus work, while its analytics and ad pixels are removed
   before the bytes leave this process.
"""

from __future__ import annotations

import pathlib
import re
from urllib.parse import unquote

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)

from tokn_console import auth_routes, dashboard_routes, ui
from tokn_console.config import Settings
from tokn_console.services import build_services
from tokn_console.static import HashedStaticFiles, static_url
from tokn_console.web import (
    RequestIdMiddleware,
    SecurityHeadersMiddleware,
    SessionMiddleware,
    SiteMiddleware,
)
from tokn_core.auth.hashing import new_secret
from tokn_core.site import SiteSettings

PACKAGE_ROOT = pathlib.Path(__file__).resolve().parent
STATIC = PACKAGE_ROOT / "static"
REPO_ROOT = PACKAGE_ROOT.parents[3]
SITE = REPO_ROOT / "brewkeg.dev"

TRACKERS = (
    (
        r'<script>\(self\.__next_s=self\.__next_s\|\|\[\]\)\.push\(\[0,\{.*?'
        r'"id":"meta-pixel-init"\}\]\)</script>',
        "",
    ),
    (r'<link rel="preload" href="https://www\.googletagmanager\.com[^>]*>', ""),
    (r"<script[^>]*googletagmanager[^>]*></script>", ""),
    (r"<script[^>]*connect\.facebook\.net[^>]*></script>", ""),
    (r'<noscript><img height="1" width="1"[^>]*facebook\.com/tr[^>]*></noscript>', ""),
    (r"<img[^>]*(?:facebook\.com/tr|google-analytics\.com)[^>]*>", ""),
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
    services = build_services(resolved)

    app = FastAPI(
        title="Brewkeg",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.services = services

    app.add_middleware(SiteMiddleware, store=services.site)
    app.add_middleware(
        SessionMiddleware,
        store=services.auth,
        pepper=resolved.session_secret,
        clock=services.clock,
        cookie_name=services.session_cookie,
    )
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

    api = APIRouter()
    auth_routes.install(api, services)
    dashboard_routes.install(api, services)
    app.include_router(api)

    _mount_site(app)
    return app


def _strip_trackers(html: str) -> str:
    """Their mirrors carry analytics and an ad pixel. Ours must not."""
    for pattern, replacement in TRACKERS:
        html = re.sub(pattern, replacement, html, flags=re.DOTALL)
    return html


def _rewrite_links(html: str) -> str:
    """Make the mirror portable: no absolute self links, no ``.html``, no variant URLs."""
    html = re.sub(r'(href|src|action)="https://brewkeg\.dev', r'\1="', html)
    html = re.sub(r'href="signup[0-9a-f]*\.html', 'href="signup', html)
    html = re.sub(r'href="index\.html"', 'href="/"', html)
    return re.sub(r'href="(?!https?:|mailto:|#)([^"]+?)\.html(#[^"]*)?"', r'href="/\1\2"', html)


CODE_COLOURS = {
    "#FFA657": "#9EC2E6",
    "#FF7B72": "#7FA8D4",
    "#D2A8FF": "#B7D0E8",
    "#79C0FF": "#8FB8DE",
    "#A5D6FF": "#A9CBE8",
    "#3FB950": "#7FB89D",
    "#56D364": "#7FB89D",
    "#E3B341": "#A9CBE8",
    "#F0883E": "#8FB8DE",
    "#FFA198": "#8FB8DE",
    "#C2703E": "#17497F",
    "#D97757": "#17497F",
    "#E8A46B": "#FFFFFF",
}
INLINE_COLOUR = re.compile(r"color:(#[0-9A-Fa-f]{6})")


def _retheme_code(html: str) -> str:
    """The code samples carry GitHub's orange, pink and purple inline colours.

    They are the loudest thing on the docs pages, so they are mapped to the same blue
    family the rest of the site uses. Only known syntax colours are touched.
    """
    return INLINE_COLOUR.sub(
        lambda match: "color:"
        + CODE_COLOURS.get(match.group(1).upper(), match.group(1)),
        html,
    )


SITE_HEADER = re.compile(r"<header\b.*?</header>", re.DOTALL)
BODY_OPEN = re.compile(r"<body\b[^>]*>", re.IGNORECASE)


def _unify_nav(html: str, header: str) -> str:
    """Put the same header on every mirrored page.

    The mirror has at least four variants: no Blog, Blog, a More menu, and a different
    call to action. The first ``<header>`` that actually contains a nav is the site header
    and is replaced. Pages without one get the header immediately after ``<body>`` so an
    article's own ``<header>`` is never mistaken for the site shell.
    """
    match = SITE_HEADER.search(html)
    if match is not None and "<nav" in match.group(0):
        return html[: match.start()] + header + html[match.end() :]
    return BODY_OPEN.sub(lambda open_tag: open_tag.group(0) + header, html, count=1)


def _serve_html(path: pathlib.Path, header: str) -> HTMLResponse:
    html = _strip_trackers(path.read_text(encoding="utf-8", errors="replace"))
    html = _rewrite_links(html)
    html = _retheme_code(html)
    html = _unify_nav(html, header)
    # theme.css first, so the operator's tokens are the ones site-extras and the design read.
    links = (
        '<link rel="stylesheet" href="/_next/static/chunks/3zpd9_1z14_om.css">'
        '<link rel="stylesheet" href="/_next/static/chunks/3r8y4op_zsama.css">'
        '<link rel="stylesheet" href="/theme.css">'
        f'<link rel="stylesheet" href="{static_url("css/site-extras.css")}">'
        f'<script src="{static_url("js/site.js")}" defer></script>'
    )
    if "</head>" in html:
        html = html.replace("</head>", f"{links}</head>", 1)
    return HTMLResponse(html, headers={"cache-control": "no-store"})


def _new_nonce() -> str:
    # A per-response marker, not a CSP nonce. It switches the middleware to the static
    # page policy; the value itself is never printed.
    return new_secret(18)


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
        nonce = _new_nonce()
        request.state.csp_nonce = nonce
        return _serve_html(index, ui.site_header(settings))

    @app.get("/_next/image", include_in_schema=False)
    async def next_image(url: str = "") -> Response:
        wanted = unquote(url).lstrip("/")
        if (SITE / wanted).is_file():
            return RedirectResponse(f"/{wanted}")
        for fallback in ("icon-192.png", "icon-512.png"):
            if (SITE / fallback).is_file():
                return RedirectResponse(f"/{fallback}")
        raise HTTPException(status_code=404)

    @app.get("/{page:path}", include_in_schema=False)
    async def site_path(request: Request, page: str) -> Response:
        """One handler for pages, nested clean URLs and every static asset.

        It replaces a plain ``StaticFiles`` mount so the same tracker stripping, theme
        injection and clean URL rules apply two directories deep as on the home page.
        """
        if page.endswith(".html"):
            return RedirectResponse(f"/{page[: -len('.html')]}", status_code=301)
        if not page:
            return RedirectResponse("/", status_code=302)

        settings: SiteSettings = request.state.settings
        target = (SITE / page).resolve()
        if SITE not in target.parents and target != SITE:
            raise HTTPException(status_code=404)

        document = SITE / f"{page}.html"
        if not document.is_file() and target.is_dir():
            candidate = target / "index.html"
            if candidate.is_file():
                document = candidate
        if document.is_file():
            if not settings.available:
                return HTMLResponse(
                    MAINTENANCE.format(name=settings.name, message=settings.maintenance_message),
                    status_code=503,
                )
            request.state.csp_nonce = _new_nonce()
            return _serve_html(document, ui.site_header(settings))

        if target.is_file():
            cache = (
                "public, max-age=31536000, immutable"
                if page.startswith("_next/static/")
                else "public, max-age=300"
            )
            return FileResponse(target, headers={"cache-control": cache})
        raise HTTPException(status_code=404)
