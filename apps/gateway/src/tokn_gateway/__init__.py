"""Tokn gateway: the data plane.

HTTP in, authenticated and metered, streamed model output out. No HTML, no cookies,
no admin routes.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from tokn_gateway.auth import GatewayAuth
from tokn_gateway.proxy import Proxy, build_router


def create_app(
    *,
    proxy: Proxy | None = None,
    auth: GatewayAuth | None = None,
    engine: object | None = None,
) -> FastAPI:
    """Build the gateway application.

    The model routes are mounted only when a working proxy is supplied, so a deployment
    without a wallet store cannot accidentally accept inference it cannot bill for.
    """

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            if engine is not None:
                await engine.dispose()  # type: ignore[attr-defined]

    app = FastAPI(
        title="Tokn Gateway",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    if proxy is not None and auth is not None:
        app.include_router(build_router(proxy, auth))

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
