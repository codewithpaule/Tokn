"""The migration must be the schema.

These tests render the migration to SQL without a database and compare it to the model
metadata, so drift between ``models`` and ``versions`` fails the build instead of
surfacing as a production incident.
"""

from __future__ import annotations

import io
from contextlib import redirect_stdout
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import CheckConstraint, UniqueConstraint

from tokn_infra.db import Base

REPO_ROOT = Path(__file__).resolve().parents[3]


def _render() -> str:
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "packages" / "infra" / "migrations"))
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        command.upgrade(config, "head", sql=True)
    return buffer.getvalue()


@pytest.fixture(scope="module")
def sql() -> str:
    return _render()


def test_every_table_is_created(sql: str) -> None:
    for table in Base.metadata.sorted_tables:
        assert f"CREATE TABLE {table.name}" in sql, f"migration is missing {table.name}"


def test_every_check_constraint_is_created(sql: str) -> None:
    missing = [
        f"{table.name}.{constraint.name}"
        for table in Base.metadata.sorted_tables
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
        and constraint.name is not None
        and constraint.name not in sql
    ]
    assert missing == []


def test_every_unique_constraint_is_created(sql: str) -> None:
    missing = [
        f"{table.name}.{constraint.name}"
        for table in Base.metadata.sorted_tables
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
        and constraint.name is not None
        and constraint.name not in sql
    ]
    assert missing == []


def test_no_constraint_name_is_double_prefixed(sql: str) -> None:
    assert "ck_ck_" not in sql
    assert "uq_uq_" not in sql


def test_money_columns_are_bigint(sql: str) -> None:
    for column in (
        "balance_micro BIGINT",
        "amount_micro BIGINT",
        "balance_after_micro BIGINT",
        "hold_micro BIGINT",
        "charged_micro BIGINT",
    ):
        assert column in sql, f"{column} must be an integer column, never float"
