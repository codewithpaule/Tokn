"""The component kit exists for review, and must not exist in production."""

from __future__ import annotations

from httpx import ASGITransport, AsyncClient
from support import BASE, FastHasher, FrozenClock, Harness

from tokn_console.app import create_app
from tokn_console.config import Settings
from tokn_infra.memory.auth_store import InMemoryAuthStore


async def test_the_kit_renders_every_component(harness: Harness) -> None:
    response = await harness.client.get("/__kit")

    assert response.status_code == 200
    for component in (
        "button--primary",
        "button--outline",
        "button--danger",
        "field-error",
        "banner--error",
        "badge--revoked",
        "spinner",
        "empty",
        "code",
    ):
        assert component in response.text, f"{component} missing from the kit"


async def test_the_kit_is_a_404_outside_development() -> None:
    app = create_app(
        Settings(env="production", session_secret="x" * 40, key_pepper="y" * 40),
        store=InMemoryAuthStore(),
        hasher=FastHasher(),
        clock=FrozenClock(),
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url=BASE) as client:
        response = await client.get("/__kit")

    assert response.status_code == 404
