"""Public pages.

Each one says only what is true today. Where a feature does not exist, the page says so
rather than showing a placeholder that looks finished.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Request, Response

from tokn_console.content.tools import TOOLS, by_slug, config_rows

if TYPE_CHECKING:
    from tokn_console.context import Console

PRIVACY_SECTIONS = [
    {
        "heading": "What we collect",
        "paragraphs": [
            "Your email address, a hash of your password, and the keys you create — "
            "stored only as hashes.",
            "A ledger of every credit and charge, and one receipt per request: model, "
            "token counts, cost, latency and outcome.",
        ],
        "placeholder": False,
    },
    {
        "heading": "What we never collect",
        "paragraphs": [
            "Prompts, completions, request bodies and authorisation headers are not "
            "stored, and there is no setting to enable storing them.",
            "There are no analytics scripts, advertising pixels or third-party font and "
            "script requests. The only cookies are functional: a session and a CSRF token.",
        ],
        "placeholder": False,
    },
    {
        "heading": "How long we keep it",
        "paragraphs": [
            "Receipts are kept so your usage stays auditable. Account data is deleted when "
            "you delete the account, except where a financial record must be retained.",
        ],
        "placeholder": True,
    },
    {
        "heading": "Your rights",
        "paragraphs": [
            "You can export your data and delete your account from Settings. For anything "
            "else, write to us.",
        ],
        "placeholder": True,
    },
]

TERMS_SECTIONS = [
    {
        "heading": "The service",
        "paragraphs": [
            "Tokn forwards model requests to upstream providers on your behalf and bills "
            "you in naira for the tokens you use.",
        ],
        "placeholder": False,
    },
    {
        "heading": "Credit",
        "paragraphs": [
            "Credit is prepaid and never expires. A request is reserved before it runs and "
            "charged after it settles. If it fails before any content arrives, nothing is "
            "charged.",
        ],
        "placeholder": False,
    },
    {
        "heading": "Acceptable use",
        "paragraphs": [
            "Do not use the service to break the law or an upstream provider's terms. Keys "
            "cannot be shared, and caps exist so a leaked key cannot run up a bill.",
        ],
        "placeholder": True,
    },
    {
        "heading": "Liability",
        "paragraphs": [
            "Model output belongs to the provider, not to us. We meter it and pass it "
            "through; we do not warrant it.",
        ],
        "placeholder": True,
    },
]


def build_router(console: Console) -> APIRouter:
    router = APIRouter()

    @router.get("/")
    async def home(request: Request) -> Response:
        return console.render(request, "public/home.html", page="home")

    @router.get("/docs")
    async def docs(request: Request) -> Response:
        return console.render(request, "public/docs.html", page="docs")

    @router.get("/support")
    async def support(request: Request) -> Response:
        return console.render(request, "public/support.html", page="support")

    @router.get("/status")
    async def status(request: Request) -> Response:
        return console.render(request, "public/status.html", page="status")

    @router.get("/privacy")
    async def privacy(request: Request) -> Response:
        return console.render(
            request,
            "public/legal.html",
            heading="Privacy",
            updated="21 November 2026",
            sections=PRIVACY_SECTIONS,
            page="privacy",
        )

    @router.get("/terms")
    async def terms(request: Request) -> Response:
        return console.render(
            request,
            "public/legal.html",
            heading="Terms",
            updated="21 November 2026",
            sections=TERMS_SECTIONS,
            page="terms",
        )

    @router.get("/pricing")
    async def pricing(request: Request) -> Response:
        return console.render(request, "public/pricing.html", catalogue=(), page="pricing")

    @router.get("/coding")
    async def coding(request: Request) -> Response:
        return console.render(
            request,
            "public/coding.html",
            heading="Connect your agent",
            lede="One key for every client. Pick yours for the exact settings.",
            tools=TOOLS,
            tool=None,
            page="coding",
        )

    @router.get("/coding/{slug}")
    async def connect_guide(request: Request, slug: str) -> Response:
        tool = by_slug(slug)
        if tool is None:
            raise HTTPException(status_code=404, detail="no such client")
        return console.render(
            request,
            "public/coding.html",
            heading=tool.name,
            lede=tool.summary,
            config=config_rows(tool, console.settings.domain),
            steps=tool.steps,
            limits=tool.limits,
            tool=tool,
            tools=TOOLS,
            page="coding",
        )

    return router
