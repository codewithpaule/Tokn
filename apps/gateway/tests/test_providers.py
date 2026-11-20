"""Provider adapters over a mocked transport: no network, no keys, real HTTP semantics."""

from __future__ import annotations

import json

import httpx
import pytest

from tokn_gateway.providers import (
    AnthropicCompatibleAdapter,
    OpenAICompatibleAdapter,
    ProviderConfig,
    UpstreamError,
    build_client,
)

OPENAI = ProviderConfig(
    name="openai",
    base_url="https://api.openai.test/v1",
    api_key="sk-test-not-real",
    allowed_hosts=frozenset({"api.openai.test"}),
)
ANTHROPIC = ProviderConfig(
    name="anthropic",
    base_url="https://api.anthropic.test/v1",
    api_key="sk-ant-test-not-real",
    allowed_hosts=frozenset({"api.anthropic.test"}),
)


def client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler), timeout=5.0, follow_redirects=False
    )


async def test_openai_sets_include_usage_and_reads_the_stream() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        seen["auth"] = request.headers["authorization"]
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=b'data: {"choices":[]}\n\ndata: [DONE]\n\n',
        )

    adapter = OpenAICompatibleAdapter(OPENAI, client(handler))
    response = await adapter.open({"model": "gpt-4o-mini", "stream": True})

    body = seen["body"]
    assert isinstance(body, dict)
    assert body["stream_options"] == {"include_usage": True}
    assert seen["auth"] == "Bearer sk-test-not-real"
    assert str(seen["url"]).endswith("/chat/completions")

    chunks = await response.aread()
    assert b"[DONE]" in chunks
    await response.aclose()


async def test_anthropic_uses_its_own_headers_and_endpoint() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["version"] = request.headers.get("anthropic-version")
        seen["key"] = request.headers.get("x-api-key")
        return httpx.Response(200, content=b'data: {"type":"message_start"}\n\n')

    adapter = AnthropicCompatibleAdapter(ANTHROPIC, client(handler))
    response = await adapter.open({"model": "claude-sonnet", "stream": True})

    assert str(seen["url"]).endswith("/messages")
    assert seen["version"] == "2023-06-01"
    assert seen["key"] == "sk-ant-test-not-real"
    await response.aclose()


async def test_a_500_is_raised_before_any_content() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, content=b'{"error":"boom"}')

    adapter = OpenAICompatibleAdapter(OPENAI, client(handler))
    with pytest.raises(UpstreamError) as caught:
        await adapter.open({"stream": True})

    assert caught.value.status == 500
    assert "boom" in caught.value.detail


async def test_authentication_errors_surface_as_upstream_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, content=b'{"error":"bad key"}')

    adapter = AnthropicCompatibleAdapter(ANTHROPIC, client(handler))
    with pytest.raises(UpstreamError) as caught:
        await adapter.open({"stream": True})
    assert caught.value.status == 401


async def test_redirects_are_not_followed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/chat/completions"):
            return httpx.Response(307, headers={"location": "https://evil.test/steal"})
        return httpx.Response(200, content=b"should not be reached")

    adapter = OpenAICompatibleAdapter(OPENAI, client(handler))
    with pytest.raises(UpstreamError) as caught:
        await adapter.open({"stream": True})
    assert caught.value.status == 307, "a redirect is a failure, not something to follow"


def test_a_host_outside_the_allowlist_is_refused_at_construction() -> None:
    sneaky = ProviderConfig(
        name="evil",
        base_url="https://attacker.test/v1",
        api_key="x",
        allowed_hosts=frozenset({"api.openai.test"}),
    )
    with pytest.raises(ValueError, match="allowlist"):
        OpenAICompatibleAdapter(sneaky, build_client())


def test_plain_http_is_refused() -> None:
    insecure = ProviderConfig(
        name="openai",
        base_url="http://api.openai.test/v1",
        api_key="x",
        allowed_hosts=frozenset({"api.openai.test"}),
    )
    with pytest.raises(ValueError, match="https"):
        AnthropicCompatibleAdapter(insecure, build_client())


def test_the_shared_client_does_not_follow_redirects() -> None:
    assert build_client().follow_redirects is False
