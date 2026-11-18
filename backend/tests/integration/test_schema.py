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
    assert await _table_count(scratch_engine) == len(Base.metadata.tables)
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
    assert await _table_count(scratch_engine) == len(Base.metadata.tables)


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


async def test_seeded_measures_match_the_code(owner_engine: AsyncEngine) -> None:
    """Migration 0006 carries a frozen copy of the measure seed; it must match the code."""
    from meip.financial.measures import MEASURES

    async with owner_engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT code, subject_kind, value_type, temporal_kind, description "
                    "FROM measure_definitions"
                )
            )
        ).all()
    seeded = {tuple(r) for r in rows}
    expected = {
        (m.code, m.subject_kind.value, m.value_type.value, m.temporal_kind.value, m.description)
        for m in MEASURES
    }
    assert seeded == expected


async def test_declared_figures_are_migrated_into_the_financial_model(
    scratch_engine: AsyncEngine,
) -> None:
    await run_alembic(scratch_engine, "downgrade", "base")
    await run_alembic(scratch_engine, "upgrade", "0005")
    seed = (
        "INSERT INTO tenants (id, name, status, created_at, updated_at) "
        "VALUES ('00000000-0000-7000-8000-000000000001', 'Firm', 'active', now(), now())",
        "INSERT INTO cases (id, tenant_id, purpose, status, created_at, updated_at,"
        " status_changed_at, version) VALUES ('00000000-0000-7000-8000-000000000002',"
        " '00000000-0000-7000-8000-000000000001', 'purchase', 'draft', now(), now(), now(), 1)",
        "INSERT INTO applicants (id, tenant_id, case_id, display_order, party_role,"
        " is_primary, status, first_name, last_name, declared_gross_annual_income_minor,"
        " declared_monthly_commitments_minor, created_at, updated_at, version)"
        " VALUES ('00000000-0000-7000-8000-000000000003',"
        " '00000000-0000-7000-8000-000000000001', '00000000-0000-7000-8000-000000000002',"
        " 1, 'borrower', true, 'active', 'A', 'B', 5000000, 45000, now(), now(), 1)",
    )
    async with scratch_engine.begin() as conn:
        for statement in seed:
            await conn.execute(text(statement))
    await run_alembic(scratch_engine, "upgrade", "head")
    async with scratch_engine.connect() as conn:
        income = (
            await conn.execute(
                text(
                    "SELECT o.measure, o.money_minor, o.frequency, o.source_kind, s.kind "
                    "FROM financial_observations o JOIN income_sources s "
                    "ON s.id = o.income_source_id"
                )
            )
        ).all()
        commitments = (
            await conn.execute(
                text(
                    "SELECT o.measure, o.money_minor, o.frequency, p.applicant_id::text "
                    "FROM financial_observations o JOIN commitments c ON c.id = o.commitment_id "
                    "JOIN commitment_parties p ON p.commitment_id = c.id"
                )
            )
        ).all()
        columns = await conn.scalar(
            text(
                "SELECT count(*) FROM information_schema.columns "
                "WHERE table_name = 'applicants' AND column_name LIKE 'declared%'"
            )
        )
    assert [tuple(r) for r in income] == [
        ("total_gross_income", 5000000, "annual", "declared", "other")
    ]
    assert [tuple(r) for r in commitments] == [
        ("monthly_payment", 45000, "monthly", "00000000-0000-7000-8000-000000000003")
    ]
    assert columns == 0
