"""Build the gateway from the environment. ``uvicorn tokn_gateway.asgi:app``."""

from __future__ import annotations

from datetime import timedelta
from urllib.parse import urlparse

from fastapi import FastAPI

from tokn_core.circuit import CircuitBreaker
from tokn_core.ratelimit import LimitPolicy
from tokn_gateway import create_app
from tokn_gateway.auth import GatewayAuth
from tokn_gateway.config import GatewaySettings
from tokn_gateway.providers import (
    AnthropicCompatibleAdapter,
    OpenAICompatibleAdapter,
    ProviderAdapter,
    ProviderConfig,
    build_client,
)
from tokn_gateway.proxy import Proxy
from tokn_infra.clock import SystemClock
from tokn_infra.memory.key_store import InMemoryKeyCache, InMemoryRateLimiter
from tokn_infra.postgres import (
    PostgresKeyStore,
    PostgresWalletStore,
    create_engine,
    create_session_factory,
)
from tokn_infra.postgres.catalogue import PostgresCatalogue, PostgresUsageSink


def build_app(settings: GatewaySettings | None = None) -> FastAPI:
    config = (settings or GatewaySettings()).validated()  # type: ignore[call-arg]
    clock = SystemClock()
    engine = create_engine(config.database_url, pool_size=config.database_pool_size)
    factory = create_session_factory(engine)

    client = build_client()
    adapters: dict[str, ProviderAdapter] = {}
    breakers: dict[str, CircuitBreaker] = {}
    if config.openai_api_key:
        adapters["openai"] = OpenAICompatibleAdapter(
            ProviderConfig(
                name="openai",
                base_url=config.openai_base_url,
                api_key=config.openai_api_key,
                allowed_hosts=frozenset({_host(config.openai_base_url)}),
            ),
            client,
        )
        breakers["openai"] = CircuitBreaker(
            failure_threshold=config.breaker_threshold,
            reset_timeout=_seconds(config.breaker_reset_seconds),
        )
    if config.anthropic_api_key:
        adapters["anthropic"] = AnthropicCompatibleAdapter(
            ProviderConfig(
                name="anthropic",
                base_url=config.anthropic_base_url,
                api_key=config.anthropic_api_key,
                allowed_hosts=frozenset({_host(config.anthropic_base_url)}),
            ),
            client,
        )
        breakers["anthropic"] = CircuitBreaker(
            failure_threshold=config.breaker_threshold,
            reset_timeout=_seconds(config.breaker_reset_seconds),
        )

    key_store = PostgresKeyStore(factory)
    proxy = Proxy(
        wallet=PostgresWalletStore(factory),
        usage_sink=PostgresUsageSink(factory),
        catalogue=PostgresCatalogue(factory),
        adapters=adapters,
        breakers=breakers,
        key_store=key_store,
        clock=clock,
    )
    auth = GatewayAuth(
        store=key_store,
        cache=InMemoryKeyCache(clock),
        limiter=InMemoryRateLimiter(),
        clock=clock,
        pepper=config.key_pepper,
        key_policy=LimitPolicy(capacity=config.burst, refill_per_second=config.requests_per_second),
        account_policy=LimitPolicy(
            capacity=config.account_burst, refill_per_second=config.account_requests_per_second
        ),
        product=config.key_prefix,
    )
    return create_app(proxy=proxy, auth=auth, engine=engine)


def _host(url: str) -> str:
    return urlparse(url).hostname or ""


def _seconds(value: int) -> timedelta:
    return timedelta(seconds=value)


app = build_app()
