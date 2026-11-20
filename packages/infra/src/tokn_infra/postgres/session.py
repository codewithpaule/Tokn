"""Async engine and session factory.

One engine per process. ``pool_pre_ping`` costs a round trip only when a connection has
gone stale, which is the failure mode that matters on a platform that rotates hosts.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

SessionFactory = async_sessionmaker[AsyncSession]


def create_engine(url: str, *, pool_size: int = 5) -> AsyncEngine:
    if not url.startswith("postgresql+asyncpg://"):
        raise ValueError("Tokn requires postgresql+asyncpg URLs")
    return create_async_engine(url, pool_size=pool_size, max_overflow=2, pool_pre_ping=True)


def create_session_factory(engine: AsyncEngine) -> SessionFactory:
    return async_sessionmaker(engine, expire_on_commit=False)
