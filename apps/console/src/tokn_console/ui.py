"""The console's HTML, rendered through one base template.

The base owns the landing page's nav and footer, the theme stylesheet order, and the
scripts. This module only escapes values and hands finished fragments to Jinja, so a
change to the shell happens in one file for every new screen.
"""

from __future__ import annotations

from datetime import datetime
from html import escape
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from tokn_console.static import static_url
from tokn_core.auth.store import AccountRecord
from tokn_core.money import Naira
from tokn_core.site import SiteSettings

CSRF_FIELD = "csrf_token"
TEMPLATES = Environment(
    loader=FileSystemLoader(Path(__file__).resolve().parent / "templates"),
    autoescape=select_autoescape(("html", "xml")),
    trim_blocks=True,
    lstrip_blocks=True,
)


def e(value: object) -> str:
    return escape(str(value), quote=True)


def naira(micro: int) -> str:
    return Naira(micro).format()


def when(value: datetime | None) -> str:
    if value is None:
        return "never"
    return value.strftime("%d %b %Y, %H:%M UTC")


def csrf_field(token: str) -> str:
    return f'<input type="hidden" name="{CSRF_FIELD}" value="{e(token)}">'


def _assets() -> dict[str, str]:
    return {
        "css_console": static_url("css/console.css"),
        "css_site": static_url("css/site-extras.css"),
        "js_console": static_url("js/console.js"),
        "js_site": static_url("js/site.js"),
    }


def site_header(settings: SiteSettings, account: AccountRecord | None = None) -> str:
    """The one public header. Served on our pages and injected into every mirrored page."""
    return TEMPLATES.get_template("_site_header.html").render(
        settings=settings,
        account=account,
    )


def page(
    settings: SiteSettings,
    *,
    title: str,
    description: str,
    body: str,
    path: str,
    csrf: str,
    account: AccountRecord | None = None,
) -> str:
    """A public page in the landing shell."""
    del path, csrf
    return TEMPLATES.get_template("public.html").render(
        settings=settings,
        title=title,
        description=description,
        content=body,
        account=account,
        **_assets(),
    )


def auth_page(
    settings: SiteSettings,
    *,
    title: str,
    kicker: str,
    heading: str,
    lead: str,
    body: str,
    aside_heading: str,
    aside_body: str,
    aside_points: tuple[tuple[str, str], ...],
    foot: str,
) -> str:
    return TEMPLATES.get_template("auth.html").render(
        settings=settings,
        title=title,
        description=lead,
        kicker=kicker,
        heading=heading,
        lead=lead,
        content=body,
        aside_heading=aside_heading,
        aside_body=aside_body,
        aside_points=aside_points,
        foot=foot,
        account=None,
        **_assets(),
    )


NAV: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    (
        "",
        (
            ("/dashboard", "Overview"),
            ("/dashboard/setup", "Set up your harness"),
            ("/dashboard/playground", "Playground"),
            ("/dashboard/requests", "Requests"),
            ("/dashboard/context", "Context health"),
        ),
    ),
    (
        "Build",
        (
            ("/dashboard/keys", "API keys"),
            ("/dashboard/experiments", "Experiments"),
        ),
    ),
    (
        "Account",
        (
            ("/dashboard/billing", "Billing"),
            ("/dashboard/referrals", "Referrals"),
            ("/dashboard/team", "Team"),
            ("/dashboard/uncensored", "Uncensored models"),
            ("/dashboard/data-protection", "Data protection"),
            ("/dashboard/settings", "Settings"),
        ),
    ),
    (
        "Help",
        (
            ("/dashboard/feedback", "Suggestion box"),
            ("/support", "Ask a question"),
        ),
    ),
)


def dashboard_page(
    settings: SiteSettings,
    *,
    account: AccountRecord,
    path: str,
    title: str,
    body: str,
    csrf: str,
    balance_micro: int,
    subtitle: str = "",
    eyebrow: str = "Console",
) -> str:
    return TEMPLATES.get_template("dashboard.html").render(
        settings=settings,
        title=title,
        description=subtitle or f"{settings.name} console",
        account=account,
        path=path,
        content=body,
        balance_text=naira(balance_micro),
        csrf_field=csrf_field(csrf),
        subtitle=subtitle,
        eyebrow=eyebrow,
        nav=NAV,
        **_assets(),
    )
