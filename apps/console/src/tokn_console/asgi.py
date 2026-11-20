"""ASGI entry point. ``uvicorn tokn_console.asgi:app``."""

from __future__ import annotations

from tokn_console.app import create_app

app = create_app()
