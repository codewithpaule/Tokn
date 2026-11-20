from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from tokn_console import create_app


@pytest.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://console.test") as http:
        yield http


async def test_healthz_is_live(client: AsyncClient) -> None:
    response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_openapi_is_not_served(client: AsyncClient) -> None:
    response = await client.get("/openapi.json")
    assert response.status_code == 404
