"""The console's own security contract.

These are the promises a customer relies on: a session after sign up, no dashboard without
it, a key shown once, and a key that belongs to someone else is not even acknowledged.
"""

from __future__ import annotations

import re

import httpx
import pytest

from tokn_console.app import create_app

ORIGIN = "http://testserver"


def _csrf(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match is not None
    return match.group(1)


async def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url=ORIGIN
    )


async def _signup(client: httpx.AsyncClient, email: str) -> None:
    page = await client.get("/signup")
    response = await client.post(
        "/signup",
        data={
            "csrf_token": _csrf(page.text),
            "email": email,
            "password": "a-long-passphrase-2026",
            "password-confirm": "a-long-passphrase-2026",
        },
        headers={"Origin": ORIGIN},
    )
    assert response.status_code == 303


async def test_signup_creates_a_session_and_a_wallet() -> None:
    async with await _client() as client:
        await _signup(client, "one@example.com")
        assert "tokn-session" in client.cookies

        dashboard = await client.get("/dashboard")
        assert dashboard.status_code == 200
        assert "one@example.com" in dashboard.text

        wallet = await client.get("/api/wallet")
        assert wallet.status_code == 200
        assert wallet.json()["balance_micro"] == 0


async def test_dashboard_requires_a_session() -> None:
    async with await _client() as client:
        response = await client.get("/dashboard")
        assert response.status_code == 303
        assert response.headers["location"].startswith("/login")


async def test_login_rejects_a_wrong_password() -> None:
    async with await _client() as client:
        await _signup(client, "two@example.com")
        client.cookies.clear()
        page = await client.get("/login")
        response = await client.post(
            "/login",
            data={
                "csrf_token": _csrf(page.text),
                "email": "two@example.com",
                "password": "not-the-right-password",
            },
            headers={"Origin": ORIGIN},
        )
        assert response.status_code == 400
        assert "not correct" in response.text
        assert "tokn-session" not in client.cookies


async def test_a_form_without_csrf_is_refused() -> None:
    async with await _client() as client:
        response = await client.post(
            "/login",
            data={"email": "someone@example.com", "password": "a-long-passphrase-2026"},
            headers={"Origin": ORIGIN},
        )
        assert response.status_code == 403


async def test_a_key_is_shown_once_and_never_listed() -> None:
    async with await _client() as client:
        await _signup(client, "keys@example.com")
        keys_page = await client.get("/dashboard/keys")
        response = await client.post(
            "/api/keys",
            data={"csrf_token": _csrf(keys_page.text), "name": "laptop"},
            headers={"Origin": ORIGIN},
        )
        assert response.status_code == 200
        match = re.search(r"(brewkeg_live_[0-9a-f]{64})", response.text)
        assert match is not None
        raw = match.group(1)

        listing = await client.get("/api/keys")
        assert listing.status_code == 200
        assert raw not in listing.text
        assert listing.json()["keys"][0]["name"] == "laptop"


async def test_one_account_cannot_revoke_another_accounts_key() -> None:
    async with await _client() as client:
        await _signup(client, "owner@example.com")
        keys_page = await client.get("/dashboard/keys")
        created = await client.post(
            "/api/keys",
            data={"csrf_token": _csrf(keys_page.text), "name": "mine"},
            headers={"Origin": ORIGIN},
        )
        key_id = re.search(r"/api/keys/([^/]+)/revoke", created.text)
        assert key_id is not None

        second = await _client()
        async with second:
            await _signup(second, "other@example.com")
            page = await second.get("/dashboard/keys")
            response = await second.post(
                f"/api/keys/{key_id.group(1)}/revoke",
                data={"csrf_token": _csrf(page.text)},
                headers={"Origin": ORIGIN},
            )
            assert response.status_code == 404


async def test_admin_page_is_guarded_and_theme_changes_are_public() -> None:
    async with await _client() as client:
        await _signup(client, "admin@example.com")
        admin = await client.get("/admin")
        assert admin.status_code == 200

        response = await client.post(
            "/admin",
            data={
                "csrf_token": _csrf(admin.text),
                "only": "theme",
                "color_ink": "#101820",
                "color_paper": "#FAFBFD",
                "color_accent": "#205F45",
                "color_accent_deep": "#174A35",
                "color_highlight": "#1A4C86",
                "font": "montserrat",
            },
            headers={"Origin": ORIGIN},
        )
        assert response.status_code == 303
        theme = await client.get("/theme.css")
        assert "--ink:#101820" in theme.text

        account = await client.get("/api/auth/session")
        assert account.json()["account"]["is_admin"] is True


@pytest.mark.parametrize("path", ["/dashboard", "/dashboard/keys", "/admin"])
async def test_html_pages_send_a_strict_script_policy(path: str) -> None:
    async with await _client() as client:
        response = await client.get(path)
        policy = response.headers["content-security-policy"]
        assert "script-src 'self'" in policy
        assert "unsafe-inline" not in policy.split("style-src")[0]
