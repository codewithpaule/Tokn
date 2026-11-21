"""The signed-in landing page.

Split out of the single routes module in UI-1. Behaviour is unchanged; every dependency
is reached through ``console`` rather than through a closure.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import RedirectResponse

if TYPE_CHECKING:
    from tokn_console.context import Console


def build_router(console: Console) -> APIRouter:
    router = APIRouter()

    @router.get("/__kit")
    async def component_kit(request: Request) -> Response:
        """Every component in every state. 404 outside development."""
        if console.settings.env != "development":
            raise HTTPException(status_code=404, detail="not found")
        return console.render(request, "kit.html")

    # ------------------------------------------------------------------- app

    @router.get("/app")
    async def overview(request: Request) -> Response:
        account = await console.current_account(request)
        if account is None:
            return RedirectResponse("/login", status_code=303)
        return console.render(request, "app.html", account=account)

    return router
