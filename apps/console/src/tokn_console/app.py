"""The console application factory.

Dependencies are injected, so tests supply an in-memory store and a frozen clock while
production picks PostgreSQL the moment ``TOKN_DATABASE_URL`` is set.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import AsyncEngine
from starlette.templating import Jinja2Templates

from tokn_console.config import Settings
from tokn_console.routes import build_router
from tokn_console.web import SecurityHeadersMiddleware, SessionMiddleware
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

PACKAGE_ROOT = Path(__file__).resolve().parent
TEMPLATES = PACKAGE_ROOT / "templates"
STATIC = PACKAGE_ROOT / "static"


def create_app(
    settings: Settings | None = None,
    *,
    store: AuthStore | None = None,
    key_store: KeyStore | None = None,
    key_cache: KeyCache | None = None,
    hasher: PasswordHasher | None = None,
    clock: Clock | None = None,
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
    app.add_middleware(SessionMiddleware, store=store, pepper=resolved.session_secret, clock=clock)
    app.add_middleware(SecurityHeadersMiddleware, hsts=resolved.enforce_https)
    app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")
    app.include_router(
        build_router(
            store=store,
            key_store=key_store,
            key_cache=key_cache,
            hasher=hasher,
            clock=clock,
            settings=resolved,
            templates=templates,
        )
    )

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app
