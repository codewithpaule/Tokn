"""Upstream provider adapters.

A passthrough: the customer's request body goes upstream unchanged, except for the few
fields that make usage reportable. Translation between protocols is a later phase, and
until it exists we do not pretend to offer it.

Every adapter refuses to talk to a host that is not on the catalog allowlist, and none of
them follows a redirect.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlparse

import httpx

CONNECT_TIMEOUT = 5.0
READ_TIMEOUT = 120.0


class UpstreamError(Exception):
    """The provider refused or failed the request before any content was delivered."""

    def __init__(self, status: int, detail: str) -> None:
        self.status = status
        self.detail = detail
        super().__init__(f"upstream {status}: {detail[:200]}")


@dataclass(frozen=True, slots=True)
class ProviderConfig:
    name: str
    base_url: str
    api_key: str
    allowed_hosts: frozenset[str]


class ProviderAdapter(Protocol):
    config: ProviderConfig

    async def open(self, payload: dict[str, object]) -> httpx.Response:
        """Start a streaming request. Raises UpstreamError on a non-2xx status."""


def build_timeout() -> httpx.Timeout:
    return httpx.Timeout(connect=CONNECT_TIMEOUT, read=READ_TIMEOUT, write=10.0, pool=5.0)


def assert_allowed(config: ProviderConfig) -> None:
    host = urlparse(config.base_url).hostname or ""
    if host not in config.allowed_hosts:
        raise ValueError(f"{host!r} is not in the allowlist for {config.name}")
    if not config.base_url.startswith("https://"):
        raise ValueError(f"{config.name} must use https")


class _Base:
    protocol = "openai"

    def __init__(self, config: ProviderConfig, client: httpx.AsyncClient) -> None:
        assert_allowed(config)
        self.config = config
        self._client = client

    def headers(self) -> dict[str, str]:
        raise NotImplementedError

    def body(self, payload: dict[str, object]) -> dict[str, object]:
        raise NotImplementedError

    def endpoint(self) -> str:
        raise NotImplementedError

    async def open(self, payload: dict[str, object]) -> httpx.Response:
        request = self._client.build_request(
            "POST",
            self.config.base_url.rstrip("/") + self.endpoint(),
            json=self.body(payload),
            headers=self.headers(),
        )
        response = await self._client.send(request, stream=True)
        if response.status_code < 200 or response.status_code >= 300:
            detail = (await response.aread()).decode("utf-8", "replace")
            await response.aclose()
            raise UpstreamError(response.status_code, detail)
        return response


class OpenAICompatibleAdapter(_Base):
    protocol = "openai"

    def headers(self) -> dict[str, str]:
        return {
            "authorization": f"Bearer {self.config.api_key}",
            "content-type": "application/json",
            "accept": "text/event-stream",
        }

    def body(self, payload: dict[str, object]) -> dict[str, object]:
        body = dict(payload)
        if body.get("stream"):
            raw = body.get("stream_options")
            options: dict[str, object] = dict(raw) if isinstance(raw, dict) else {}
            options["include_usage"] = True
            body["stream_options"] = options
        return body

    def endpoint(self) -> str:
        return "/chat/completions"


class AnthropicCompatibleAdapter(_Base):
    protocol = "anthropic"

    def headers(self) -> dict[str, str]:
        return {
            "x-api-key": self.config.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
            "accept": "text/event-stream",
        }

    def body(self, payload: dict[str, object]) -> dict[str, object]:
        body = dict(payload)
        if body.get("stream"):
            body["stream"] = True
        return body

    def endpoint(self) -> str:
        return "/messages"


def build_client() -> httpx.AsyncClient:
    """Shared client. Redirects are never followed, and the timeout is the catalog one."""
    return httpx.AsyncClient(timeout=build_timeout(), follow_redirects=False)
