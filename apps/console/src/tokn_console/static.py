"""Static asset URLs with a content hash.

The hash is the file's own digest, so a changed stylesheet is a different URL and a year
of caching is safe. Nothing is built; the hash is computed once per file per process.
"""

from __future__ import annotations

import hashlib
from collections.abc import MutableMapping
from pathlib import Path
from typing import Any

from starlette.responses import Response
from starlette.staticfiles import StaticFiles

STATIC_ROOT = Path(__file__).resolve().parent / "static"
_versions: dict[str, str] = {}


def static_url(path: str) -> str:
    """Return ``/static/<path>?v=<12 hex of sha256>``.

    A missing file yields the plain URL with no version rather than raising: a broken
    reference should show up in review, not take the page down.
    """
    clean = path.lstrip("/")
    version = _versions.get(clean)
    if version is None:
        target = STATIC_ROOT / clean
        if not target.is_file():
            return f"/static/{clean}"
        version = hashlib.sha256(target.read_bytes()).hexdigest()[:12]
        _versions[clean] = version
    return f"/static/{clean}?v={version}"


class HashedStaticFiles(StaticFiles):
    """Static files are immutable: every URL carries the content hash."""

    async def get_response(self, path: str, scope: MutableMapping[str, Any]) -> Response:
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            response.headers["cache-control"] = "public, max-age=31536000, immutable"
        return response
