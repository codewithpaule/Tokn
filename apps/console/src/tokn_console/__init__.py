"""Tokn console: the control plane.

Accounts, sessions, API keys, billing and admin. Server-rendered HTML only. It never
proxies model traffic.
"""

from __future__ import annotations

from fastapi import FastAPI


def create_app() -> FastAPI:
    """Build the console application."""
    app = FastAPI(
        title="Tokn Console",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
