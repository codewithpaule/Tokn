"""Console routers, split by area.

The public marketing pages now come from the static site mounted at /, so this package
only carries the application itself: auth, keys, overview and admin.

Each module exposes ``build_router(console)``; this package composes them into the single
router the application mounts.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter

from tokn_console.routes import admin, auth, keys, overview

if TYPE_CHECKING:
    from tokn_console.context import Console


def build_router(console: Console) -> APIRouter:
    router = APIRouter()
    router.include_router(auth.build_router(console))
    router.include_router(overview.build_router(console))
    router.include_router(keys.build_router(console))
    router.include_router(admin.build_router(console))
    return router


__all__ = ["build_router"]
