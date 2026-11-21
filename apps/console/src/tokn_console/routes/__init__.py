"""Console routers, split by area.

Each module exposes ``build_router(console)``; this package composes them into the single
router the application mounts.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter

from tokn_console.routes import auth, keys, overview, public

if TYPE_CHECKING:
    from tokn_console.context import Console


def build_router(console: Console) -> APIRouter:
    router = APIRouter()
    router.include_router(public.build_router(console))
    router.include_router(auth.build_router(console))
    router.include_router(overview.build_router(console))
    router.include_router(keys.build_router(console))
    return router


__all__ = ["build_router"]
