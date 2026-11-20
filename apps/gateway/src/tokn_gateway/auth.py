"""Gateway authentication.

The data plane has no HTML, no cookies and no admin surface. A request either carries a
key that resolves to a live record, or it gets one undifferentiated 401. Scope, address
and rate-limit failures are distinguishable on purpose: they tell a legitimate customer
what to fix without telling an attacker whether a key exists.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from fastapi import HTTPException, Request, status

from tokn_core.api_keys import KeyRecord
from tokn_core.auth.ports import Clock
from tokn_core.errors import AuthenticationFailedError
from tokn_core.key_auth import KeyCache, KeyStore, authenticate
from tokn_core.ratelimit import LimitPolicy


class RateLimiter(Protocol):
    async def consume(
        self, key: str, *, policy: LimitPolicy, now: datetime, cost: int = 1
    ) -> tuple[bool, int]: ...

    async def acquire(self, key: str, *, limit: int) -> bool: ...

    async def release(self, key: str) -> None: ...


def presented_key(request: Request) -> str | None:
    """Read the key from the two headers clients actually use. Never from a query string."""
    header = request.headers.get("authorization")
    bearer = None
    if header:
        scheme, _, value = header.partition(" ")
        if scheme.lower() == "bearer" and value:
            bearer = value.strip()
    api_key = request.headers.get("x-api-key")
    api_key = api_key.strip() if api_key else None

    if bearer and api_key and bearer != api_key:
        return None  # contradictory credentials are not a credential
    return bearer or api_key


def client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("fly-client-ip") or request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


def _unauthorised() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="invalid api key",
        headers={"WWW-Authenticate": "Bearer"},
    )


@dataclass(slots=True, eq=False)
class GatewayAuth:
    """A FastAPI dependency that resolves the caller's key."""

    store: KeyStore
    cache: KeyCache
    limiter: RateLimiter
    clock: Clock
    pepper: str
    key_policy: LimitPolicy
    account_policy: LimitPolicy
    product: str = "tokn"

    async def __call__(self, request: Request) -> KeyRecord:
        raw = presented_key(request)
        if raw is None:
            raise _unauthorised()

        try:
            record = await authenticate(
                presented=raw,
                store=self.store,
                cache=self.cache,
                pepper=self.pepper,
                clock=self.clock,
                product=self.product,
            )
        except AuthenticationFailedError:
            raise _unauthorised() from None

        if not record.has_scope():
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"this key lacks the {record.prefix} scope",
            )
        if not record.permits(client_ip(request)):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="this key is not allowed from this address",
            )

        await self._rate_limit(f"key:{record.id}", self.key_policy)
        await self._rate_limit(f"account:{record.account_id}", self.account_policy)
        return record

    async def _rate_limit(self, bucket: str, policy: LimitPolicy) -> None:
        allowed, retry_after = await self.limiter.consume(
            bucket, policy=policy, now=self.clock.now()
        )
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="rate limit exceeded",
                headers={"Retry-After": str(retry_after)},
            )

    @asynccontextmanager
    async def slot(self, record: KeyRecord) -> AsyncIterator[None]:
        """Hold one concurrency slot for the life of a request."""
        if not await self.limiter.acquire(
            f"concurrency:{record.id}", limit=record.concurrency_limit
        ):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="too many concurrent requests for this key",
            )
        try:
            yield
        finally:
            await self.limiter.release(f"concurrency:{record.id}")
