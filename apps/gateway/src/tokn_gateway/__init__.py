"""Tokn gateway: the data plane.

HTTP in, authenticated and metered, streamed model output out. No HTML, no cookies,
no admin routes.
"""

from __future__ import annotations

from fastapi import FastAPI


def create_app() -> FastAPI:
    """Build the gateway application."""
    app = FastAPI(
        title="Tokn Gateway",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
