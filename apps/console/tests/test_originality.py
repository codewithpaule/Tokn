"""No competitor wording, name or asset may appear anywhere in the rendered UI.

The list is deliberately literal: each phrase is a sentence from the reference site that we
must not reuse. If one of these ever lands in a template, this test fails the build rather
than a lawyer finding it.
"""

from __future__ import annotations

import pytest
from support import Harness

BANNED = (
    "brewkeg",
    "keep the agent. change the provider",
    "keep claude code or codex",
    "nothing expires",
    "four families, one key",
    "three steps, then just code",
    "connected correctly",
    "a lineup that works on the first launch",
    "your key stays with you",
    "questions, answered",
    "a key first. tokens after",
    "the harness stays yours",
    "tokens served on",
)

PUBLIC_PAGES = (
    "/",
    "/pricing",
    "/coding",
    "/coding/claude-code",
    "/docs",
    "/support",
    "/status",
    "/privacy",
    "/terms",
    "/login",
    "/signup",
)


@pytest.mark.parametrize("path", PUBLIC_PAGES)
async def test_no_competitor_wording(harness: Harness, path: str) -> None:
    response = await harness.client.get(path)
    assert response.status_code == 200, f"{path} did not render"

    html = response.text.lower()
    found = [phrase for phrase in BANNED if phrase in html]
    assert found == [], f"{path} contains {found}"
