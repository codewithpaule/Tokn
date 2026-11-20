"""Tokn domain core.

Framework-free models, ports and use cases. This package must never import
FastAPI, SQLAlchemy, Redis, httpx or any application package.
"""

from tokn_core.money import MICRO_PER_NAIRA, Naira

__all__ = ["MICRO_PER_NAIRA", "Naira"]
__version__ = "0.1.0"
