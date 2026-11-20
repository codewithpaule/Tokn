from __future__ import annotations

import ast
from pathlib import Path

CORE_SRC = Path(__file__).resolve().parents[1] / "src" / "tokn_core"

FORBIDDEN_ROOTS = frozenset(
    {
        "fastapi",
        "starlette",
        "uvicorn",
        "sqlalchemy",
        "alembic",
        "asyncpg",
        "redis",
        "httpx",
        "jinja2",
        "pydantic",
        "tokn_infra",
        "tokn_gateway",
        "tokn_console",
    }
)


def _imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def test_core_imports_nothing_outward() -> None:
    offenders: dict[str, set[str]] = {}
    for path in sorted(CORE_SRC.rglob("*.py")):
        forbidden = _imported_roots(path) & FORBIDDEN_ROOTS
        if forbidden:
            offenders[str(path.relative_to(CORE_SRC))] = forbidden
    assert offenders == {}, f"core must stay framework-free: {offenders}"
