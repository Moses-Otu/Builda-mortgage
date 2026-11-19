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
HEAD = max(p.name[:4] for p in (BACKEND_DIR / "migrations" / "versions").glob("[0-9]*.py"))


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
                    "SELECT code, subject_kind, value_type, temporal_kind, description,"
                    " qualifier_required FROM measure_definitions"
                    # Tests add (append-only, undeletable) measures prefixed test_.
                    " WHERE code NOT LIKE 'test!_%' ESCAPE '!'"
                )
            )
        ).all()
    seeded = {tuple(r) for r in rows}
    expected = {
        (
            m.code,
            m.subject_kind.value,
            m.value_type.value,
            m.temporal_kind.value,
            m.description,
            m.qualifier_required,
        )
        for m in MEASURES
    }
    assert seeded == expected

    from meip.financial.measures import QUALIFIERS

    async with owner_engine.connect() as conn:
        qualifier_rows = (
            await conn.execute(
                text(
                    "SELECT measure_code, subject_kind, qualifier, description "
                    "FROM measure_qualifiers WHERE qualifier NOT LIKE 'test!_%' ESCAPE '!'"
                )
            )
        ).all()
    assert {tuple(r) for r in qualifier_rows} == {
        (q.measure, q.subject_kind.value, q.code, q.description) for q in QUALIFIERS
    }


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


async def _fresh(engine: AsyncEngine) -> None:
    """Recreate the scratch database. Downgrading would be data-dependent: migration 0002's
    downgrade cannot restore NOT NULL once a case exists without a creating user."""
    await engine.dispose()
    await create_database(MIGRATION_DB)


async def _seed_case_with_income(conn_engine: AsyncEngine) -> None:
    statements = (
        "INSERT INTO tenants (id, name, status, created_at, updated_at) VALUES"
        " ('00000000-0000-7000-8000-0000000000a1', 'Firm', 'active', now(), now())",
        "INSERT INTO cases (id, tenant_id, purpose, status, created_at, updated_at,"
        " status_changed_at, version) VALUES ('00000000-0000-7000-8000-0000000000a2',"
        " '00000000-0000-7000-8000-0000000000a1', 'purchase', 'draft', now(), now(), now(), 1)",
        "INSERT INTO applicants (id, tenant_id, case_id, display_order, party_role, is_primary,"
        " status, first_name, last_name, created_at, updated_at, version) VALUES"
        " ('00000000-0000-7000-8000-0000000000a3', '00000000-0000-7000-8000-0000000000a1',"
        " '00000000-0000-7000-8000-0000000000a2', 1, 'borrower', true, 'active', 'A', 'B',"
        " now(), now(), 1)",
        "INSERT INTO income_sources (id, tenant_id, case_id, applicant_id, kind, is_primary,"
        " created_at, updated_at, version) VALUES ('00000000-0000-7000-8000-0000000000a4',"
        " '00000000-0000-7000-8000-0000000000a1', '00000000-0000-7000-8000-0000000000a2',"
        " '00000000-0000-7000-8000-0000000000a3', 'employment', false, now(), now(), 1)",
    )
    async with conn_engine.begin() as conn:
        for statement in statements:
            await conn.execute(text(statement))


_BONUS = (
    "INSERT INTO financial_observations (id, tenant_id, case_id, income_source_id, measure,"
    " value_type, temporal_kind, money_minor, currency, frequency, source_kind, recorded_at)"
    " VALUES (gen_random_uuid(), '00000000-0000-7000-8000-0000000000a1',"
    " '00000000-0000-7000-8000-0000000000a2', '00000000-0000-7000-8000-0000000000a4',"
    " 'bonus', 'money', 'flow', 1, 'GBP', 'annual', 'declared', now())"
)


async def test_existing_bonus_values_are_backfilled_as_unspecified(
    scratch_engine: AsyncEngine,
) -> None:
    await _fresh(scratch_engine)
    await run_alembic(scratch_engine, "upgrade", "0007")
    await _seed_case_with_income(scratch_engine)
    async with scratch_engine.begin() as conn:
        await conn.execute(text(_BONUS))
    await run_alembic(scratch_engine, "upgrade", "head")
    async with scratch_engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT qualifier, qualifier_required FROM financial_observations")
            )
        ).one()
    assert tuple(row) == ("unspecified", True)


async def test_migration_stops_on_duplicate_current_values(scratch_engine: AsyncEngine) -> None:
    await _fresh(scratch_engine)
    await run_alembic(scratch_engine, "upgrade", "0008")
    await _seed_case_with_income(scratch_engine)
    duplicate = _BONUS.replace("measure,", "measure, qualifier, qualifier_required,").replace(
        "'bonus',", "'bonus', 'guaranteed', true,"
    )
    async with scratch_engine.begin() as conn:
        await conn.execute(text(duplicate))
        await conn.execute(text(duplicate))
    with pytest.raises(Exception, match="Duplicate current financial values"):
        await run_alembic(scratch_engine, "upgrade", "0009")
    await _fresh(scratch_engine)


async def _state(engine: AsyncEngine, query: str) -> tuple[object, object]:
    """After a refused downgrade: the revision and the data it protected, unchanged."""
    async with engine.connect() as conn:
        revision = await conn.scalar(text("SELECT version_num FROM alembic_version"))
        value = await conn.scalar(text(query))
    return revision, value


async def test_downgrade_refuses_to_reinstate_withdrawn_items(scratch_engine: AsyncEngine) -> None:
    await _fresh(scratch_engine)
    await run_alembic(scratch_engine, "upgrade", "head")
    await _seed_case_with_income(scratch_engine)
    async with scratch_engine.begin() as conn:
        await conn.execute(
            text("UPDATE income_sources SET withdrawn_at = now(), withdrawal_reason = 'duplicate'")
        )
    with pytest.raises(Exception, match="Withdrawn items exist"):
        await run_alembic(scratch_engine, "downgrade", "0009")
    assert await _state(scratch_engine, "SELECT withdrawal_reason FROM income_sources") == (
        HEAD,
        "duplicate",
    )
    await _fresh(scratch_engine)


async def test_downgrade_refuses_to_discard_qualifiers(scratch_engine: AsyncEngine) -> None:
    await _fresh(scratch_engine)
    await run_alembic(scratch_engine, "upgrade", "head")
    await _seed_case_with_income(scratch_engine)
    qualified = _BONUS.replace("measure,", "measure, qualifier, qualifier_required,").replace(
        "'bonus',", "'bonus', 'guaranteed', true,"
    )
    async with scratch_engine.begin() as conn:
        await conn.execute(text(qualified))
    with pytest.raises(Exception, match="would discard qualifiers"):
        await run_alembic(scratch_engine, "downgrade", "0007")
    assert await _state(scratch_engine, "SELECT qualifier FROM financial_observations") == (
        HEAD,
        "guaranteed",
    )
    await _fresh(scratch_engine)


async def test_downgrade_keeps_working_without_lossy_data(scratch_engine: AsyncEngine) -> None:
    """Unspecified qualifiers carry nothing the pre-0008 schema could not represent."""
    await _fresh(scratch_engine)
    await run_alembic(scratch_engine, "upgrade", "head")
    await _seed_case_with_income(scratch_engine)
    unspecified = _BONUS.replace("measure,", "measure, qualifier, qualifier_required,").replace(
        "'bonus',", "'bonus', 'unspecified', true,"
    )
    async with scratch_engine.begin() as conn:
        await conn.execute(text(unspecified))
    await run_alembic(scratch_engine, "downgrade", "0007")
    async with scratch_engine.connect() as conn:
        assert await conn.scalar(text("SELECT count(*) FROM financial_observations")) == 1
    await _fresh(scratch_engine)


async def test_downgrade_refuses_to_orphan_stored_documents(scratch_engine: AsyncEngine) -> None:
    await _fresh(scratch_engine)
    await run_alembic(scratch_engine, "upgrade", "head")
    await _seed_case_with_income(scratch_engine)
    async with scratch_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO documents (id, tenant_id, case_id, document_type, status,"
                " created_at, updated_at, version) VALUES (gen_random_uuid(),"
                " '00000000-0000-7000-8000-0000000000a1', '00000000-0000-7000-8000-0000000000a2',"
                " 'payslip', 'received', now(), now(), 1)"
            )
        )
    with pytest.raises(Exception, match="Documents exist"):
        await run_alembic(scratch_engine, "downgrade", "0011")
    assert await _state(scratch_engine, "SELECT count(*) FROM documents") == (HEAD, 1)
    await _fresh(scratch_engine)


async def test_downgrade_refuses_to_discard_processing_state(scratch_engine: AsyncEngine) -> None:
    await _fresh(scratch_engine)
    await run_alembic(scratch_engine, "upgrade", "head")
    await _seed_case_with_income(scratch_engine)
    async with scratch_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO documents (id, tenant_id, case_id, document_type, status,"
                " failure_code, created_at, updated_at, version) VALUES (gen_random_uuid(),"
                " '00000000-0000-7000-8000-0000000000a1', '00000000-0000-7000-8000-0000000000a2',"
                " 'payslip', 'failed', 'timeout', now(), now(), 1)"
            )
        )
    with pytest.raises(Exception, match="processing state"):
        await run_alembic(scratch_engine, "downgrade", "0012")
    assert await _state(scratch_engine, "SELECT failure_code FROM documents") == (HEAD, "timeout")
    await _fresh(scratch_engine)


async def test_downgrade_refuses_to_discard_extraction_results(
    scratch_engine: AsyncEngine,
) -> None:
    await _fresh(scratch_engine)
    await run_alembic(scratch_engine, "upgrade", "head")
    await _seed_case_with_income(scratch_engine)
    async with scratch_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO documents (id, tenant_id, case_id, document_type, status,"
                " page_count, created_at, updated_at, version) VALUES (gen_random_uuid(),"
                " '00000000-0000-7000-8000-0000000000a1', '00000000-0000-7000-8000-0000000000a2',"
                " 'payslip', 'unrecognised', 1, now(), now(), 1)"
            )
        )
    with pytest.raises(Exception, match="Extraction results exist"):
        await run_alembic(scratch_engine, "downgrade", "0013")
    assert await _state(scratch_engine, "SELECT status FROM documents") == (HEAD, "unrecognised")
    await _fresh(scratch_engine)


async def test_downgrade_refuses_to_discard_reviews(scratch_engine: AsyncEngine) -> None:
    await _fresh(scratch_engine)
    await run_alembic(scratch_engine, "upgrade", "head")
    await _seed_case_with_income(scratch_engine)
    async with scratch_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO documents (id, tenant_id, case_id, document_type, status,"
                " page_count, created_at, updated_at, version) VALUES (gen_random_uuid(),"
                " '00000000-0000-7000-8000-0000000000a1', '00000000-0000-7000-8000-0000000000a2',"
                " 'payslip', 'reviewed', 1, now(), now(), 1)"
            )
        )
    with pytest.raises(Exception, match="Document reviews exist"):
        await run_alembic(scratch_engine, "downgrade", "0014")
    assert await _state(scratch_engine, "SELECT status FROM documents") == (HEAD, "reviewed")
    await _fresh(scratch_engine)
