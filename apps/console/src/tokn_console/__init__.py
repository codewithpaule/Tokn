"""Tokn console: the control plane.

Accounts, sessions, API keys, billing and admin. Server-rendered HTML only. It never
proxies model traffic.
"""

from tokn_console.app import create_app

__all__ = ["create_app"]
