"""Acceptance criteria 2 (migrations reversible, no model drift) and 10 (OpenAPI snapshot)."""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from fastapi import FastAPI
from sqlalchemy import Connection, text
from sqlalchemy.ext.asyncio import AsyncEngine

from meip.core.db import create_engine
from meip.models import Base
from tests.conftest import BACKEND_DIR, create_database, db_url, run_alembic

MIGRATION_DB = "meip_test_migrations"
SNAPSHOT = BACKEND_DIR / "openapi.json"


@pytest.fixture(scope="module")
async def scratch_engine() -> AsyncIterator[AsyncEngine]:
    await create_database(MIGRATION_DB)
    engine = create_engine(db_url("meip_owner", MIGRATION_DB), pool_size=1)
    yield engine
    await engine.dispose()


async def _table_count(engine: AsyncEngine) -> int:
    async with engine.connect() as conn:
        n = await conn.scalar(
            text(
                "SELECT count(*) FROM pg_tables WHERE schemaname = 'public' "
                "AND tablename <> 'alembic_version'"
            )
        )
    return int(n or 0)


async def test_upgrade_downgrade_upgrade(scratch_engine: AsyncEngine) -> None:
    await run_alembic(scratch_engine, "upgrade", "head")
    assert await _table_count(scratch_engine) == 6
    await run_alembic(scratch_engine, "downgrade", "base")
    assert await _table_count(scratch_engine) == 0
    async with scratch_engine.connect() as conn:
        functions = await conn.scalar(
            text(
                "SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                "WHERE n.nspname = 'public'"
            )
        )
    assert functions == 0
    await run_alembic(scratch_engine, "upgrade", "head")
    assert await _table_count(scratch_engine) == 6


async def test_models_match_migrations(scratch_engine: AsyncEngine) -> None:
    await run_alembic(scratch_engine, "upgrade", "head")

    def _diff(conn: Connection) -> list[object]:
        return list(compare_metadata(MigrationContext.configure(conn), Base.metadata))

    async with scratch_engine.connect() as conn:
        differences = await conn.run_sync(_diff)
    assert differences == []


def test_openapi_matches_snapshot(app: FastAPI) -> None:
    """Regenerate with: UPDATE_OPENAPI_SNAPSHOT=1 pytest tests/integration/test_schema.py"""
    current = json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n"
    if os.environ.get("UPDATE_OPENAPI_SNAPSHOT") == "1":
        SNAPSHOT.write_text(current, encoding="utf-8")
    assert SNAPSHOT.exists(), "Snapshot missing; run with UPDATE_OPENAPI_SNAPSHOT=1"
    assert current == SNAPSHOT.read_text(encoding="utf-8"), (
        "API contract changed. Review the diff, then regenerate the snapshot."
    )


def test_every_operation_documented(app: FastAPI) -> None:
    for path, operations in app.openapi()["paths"].items():
        for method, op in operations.items():
            assert op.get("summary"), f"{method.upper()} {path} has no summary"
            assert op.get("tags"), f"{method.upper()} {path} has no tag"
