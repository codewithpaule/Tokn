"""The site settings screen is for the operator, not for every account."""

from __future__ import annotations

from support import EMAIL, Harness, form_post, log_in, sign_up

from tokn_console.app import create_app  # noqa: F401 - keeps the import graph warm


async def _admin(harness: Harness) -> None:
    await sign_up(harness)
    await log_in(harness)
    account = await harness.store.account_by_email(EMAIL)
    assert account is not None
    await harness.store.make_admin(account.id)


async def test_anonymous_is_sent_to_sign_in(harness: Harness) -> None:
    response = await harness.client.get("/admin/site", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


async def test_a_normal_account_gets_a_404(harness: Harness) -> None:
    await sign_up(harness)
    await log_in(harness)

    response = await harness.client.get("/admin/site", follow_redirects=False)
    assert response.status_code == 404


async def test_an_admin_sees_the_settings(harness: Harness) -> None:
    await _admin(harness)

    response = await harness.client.get("/admin/site")
    assert response.status_code == 200
    assert "Site settings" in response.text
    assert 'name="available"' in response.text


async def test_the_operator_can_take_the_site_offline(harness: Harness) -> None:
    await _admin(harness)

    await form_post(
        harness.client,
        "/admin/site",
        {
            "name": "Tokn",
            "mark": "T",
            "tagline": "Your coding agent. Paid by the token.",
            "hero_body": "Point the agent you already use at one key.",
            "support_email": "support@tokn.ng",
            "maintenance_message": "Back in ten minutes.",
            "color_ink": "#0e1210",
            "color_paper": "#fbfbf8",
            "color_accent": "#1e6b4b",
            "color_accent_deep": "#154d36",
            "color_highlight": "#e3b93c",
            "font": "montserrat",
        },
    )

    home = await harness.client.get("/")
    assert home.status_code == 503
    assert "Back in ten minutes." in home.text

    admin = await harness.client.get("/admin/site")
    assert admin.status_code == 200, "the operator can still turn it back on"


async def test_a_bad_colour_is_refused_with_a_message(harness: Harness) -> None:
    await _admin(harness)

    response = await form_post(
        harness.client,
        "/admin/site",
        {
            "name": "Tokn",
            "mark": "T",
            "tagline": "x",
            "hero_body": "y",
            "support_email": "support@tokn.ng",
            "maintenance_message": "z",
            "color_ink": "not-a-colour",
            "color_paper": "#fbfbf8",
            "color_accent": "#1e6b4b",
            "color_accent_deep": "#154d36",
            "color_highlight": "#e3b93c",
            "font": "montserrat",
        },
    )

    assert response.status_code == 422
    assert "colour" in response.text
