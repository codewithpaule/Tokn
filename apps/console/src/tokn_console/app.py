"""The console application factory.

Dependencies are injected, so tests can supply an in-memory store and a frozen clock while
production wires PostgreSQL and the system clock.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
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
    if store is None:
        if resolved.env != "development":
            raise RuntimeError("a durable AuthStore must be provided outside development")
        store = InMemoryAuthStore()
    hasher = hasher or Argon2PasswordHasher()
    clock = clock or SystemClock()
    if key_store is None:
        key_store = InMemoryKeyStore()
    if key_cache is None:
        key_cache = InMemoryKeyCache(clock)

    templates = Jinja2Templates(directory=str(TEMPLATES))

    app = FastAPI(
        title="Tokn Console",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
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
