"""Admin: the site's own settings, and the stylesheet they generate.

Both routes are deliberately small. The settings route renders a form from the dataclass
fields, so adding a field does not mean editing a template.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.responses import PlainTextResponse

from tokn_console.web import require_csrf
from tokn_core.site import FONTS, SiteSettings

if TYPE_CHECKING:
    from tokn_console.context import Console

TEXT_FIELDS = (
    ("name", "Site name"),
    ("mark", "Logo letter"),
    ("tagline", "Hero headline"),
    ("hero_body", "Hero paragraph"),
    ("support_email", "Support email"),
    ("maintenance_message", "Message shown when unavailable"),
)


def build_router(console: Console) -> APIRouter:
    router = APIRouter()

    @router.get("/theme.css", include_in_schema=False)
    async def theme(request: Request) -> Response:
        settings: SiteSettings = request.state.settings
        return PlainTextResponse(
            settings.theme_css(),
            media_type="text/css",
            headers={"cache-control": "no-store"},
        )

    @router.get("/admin/site")
    async def site_form(request: Request) -> Response:
        settings: SiteSettings = request.state.settings
        return console.render(
            request,
            "admin/site.html",
            settings=settings,
            fonts=sorted(FONTS),
            text_fields=TEXT_FIELDS,
            page="admin",
        )

    @router.post("/admin/site", dependencies=[Depends(require_csrf)])
    async def site_save(
        request: Request,
        name: str = Form(""),
        mark: str = Form(""),
        tagline: str = Form(""),
        hero_body: str = Form(""),
        support_email: str = Form(""),
        maintenance_message: str = Form(""),
        color_ink: str = Form(""),
        color_paper: str = Form(""),
        color_accent: str = Form(""),
        color_accent_deep: str = Form(""),
        color_highlight: str = Form(""),
        font: str = Form("montserrat"),
        available: str = Form(""),
    ) -> Response:
        current: SiteSettings = request.state.settings
        try:
            updated = current.with_changes(
                available=available == "on",
                name=name,
                mark=mark,
                tagline=tagline,
                hero_body=hero_body,
                support_email=support_email,
                maintenance_message=maintenance_message,
                color_ink=color_ink,
                color_paper=color_paper,
                color_accent=color_accent,
                color_accent_deep=color_accent_deep,
                color_highlight=color_highlight,
                font=font,
            )
        except ValueError as error:
            return console.render(
                request,
                "admin/site.html",
                status_code=422,
                settings=current,
                fonts=sorted(FONTS),
                text_fields=TEXT_FIELDS,
                error=str(error),
                page="admin",
            )

        if console.site is None:
            raise RuntimeError("the console was built without a settings store")
        await console.site.save(updated)
        return console.redirect("/admin/site")

    return router
