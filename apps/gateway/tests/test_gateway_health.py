from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from tokn_gateway import create_app


@pytest.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://gateway.test") as http:
        yield http


async def test_healthz_is_live(client: AsyncClient) -> None:
    response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_openapi_is_not_served(client: AsyncClient) -> None:
    response = await client.get("/openapi.json")
    assert response.status_code == 404


async def test_model_routes_are_absent_without_a_proxy(client: AsyncClient) -> None:
    """A deployment with no wallet store must not accept inference it cannot bill."""
    response = await client.post("/v1/chat/completions", json={"model": "x"})
    assert response.status_code == 404
