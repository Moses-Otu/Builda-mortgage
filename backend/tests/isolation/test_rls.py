"""Acceptance criterion 5: database-level tenant isolation, independent of application code.

These tests connect as the runtime role with raw SQL, bypassing every application-layer
filter, to prove row-level security alone keeps tenants apart.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import asyncpg
import pytest

from tests.conftest import TEST_DB, Firm, MakeFirm, db_url
from tests.isolation.schema_policy import RLS_TABLES

# Derived from the schema classification, which test_catalog.py checks against the live
# catalog: a new tenant table is covered here automatically once it is classified.
TENANT_TABLES = sorted(RLS_TABLES)


@pytest.fixture
async def app_conn() -> AsyncIterator[asyncpg.Connection]:
    conn = await asyncpg.connect(db_url("meip_app", TEST_DB, driver="postgresql"))
    try:
        yield conn
    finally:
        await conn.close()


@pytest.fixture
async def populated(make_firm: MakeFirm) -> Firm:
    firm = await make_firm()
    case = await firm.create_case()
    await firm.add_applicant(case["id"])
    assert (await firm.upload(case["id"])).status_code == 201
    return firm


async def _set_tenant(conn: asyncpg.Connection, tenant_id: uuid.UUID | str) -> None:
    await conn.execute("SELECT set_config('app.tenant_id', $1, false)", str(tenant_id))


@pytest.mark.parametrize("table", TENANT_TABLES)
async def test_no_tenant_setting_sees_nothing(
    app_conn: asyncpg.Connection, populated: Firm, table: str
) -> None:
    assert await app_conn.fetchval(f"SELECT count(*) FROM {table}") == 0  # noqa: S608


@pytest.mark.parametrize("table", TENANT_TABLES)
async def test_wrong_tenant_sees_nothing(
    app_conn: asyncpg.Connection, populated: Firm, table: str
) -> None:
    await _set_tenant(app_conn, uuid.uuid4())
    assert await app_conn.fetchval(f"SELECT count(*) FROM {table}") == 0  # noqa: S608


async def test_own_tenant_sees_only_own_rows(
    app_conn: asyncpg.Connection, populated: Firm, make_firm: MakeFirm
) -> None:
    other = await make_firm()
    await other.create_case()

    await _set_tenant(app_conn, populated.tenant_id)
    tenants = await app_conn.fetch("SELECT DISTINCT tenant_id FROM cases")
    assert [r["tenant_id"] for r in tenants] == [populated.tenant_id]
    assert await app_conn.fetchval("SELECT count(*) FROM tenants") == 1
    assert await app_conn.fetchval("SELECT count(*) FROM applicants") == 1
    # Only identities holding a membership in this firm (here: its one admin).
    assert await app_conn.fetchval("SELECT count(*) FROM users") == 1


async def test_cannot_insert_into_another_tenant(
    app_conn: asyncpg.Connection, populated: Firm, make_firm: MakeFirm
) -> None:
    other = await make_firm()
    await _set_tenant(app_conn, populated.tenant_id)
    user_id = await app_conn.fetchval("SELECT id FROM users LIMIT 1")
    with pytest.raises(asyncpg.InsufficientPrivilegeError, match="row-level security"):
        await app_conn.execute(
            "INSERT INTO cases (id, tenant_id, purpose, status, created_by_user_id, created_at,"
            " updated_at, status_changed_at, version)"
            " VALUES ($1, $2, 'purchase', 'draft', $3, now(), now(), now(), 1)",
            uuid.uuid4(),
            other.tenant_id,
            user_id,
        )


async def test_cannot_update_or_move_rows_across_tenants(
    app_conn: asyncpg.Connection, populated: Firm, make_firm: MakeFirm
) -> None:
    other = await make_firm()
    await other.create_case()

    await _set_tenant(app_conn, populated.tenant_id)
    # Rows of the other tenant are invisible, so the update matches nothing.
    result = await app_conn.execute(
        "UPDATE cases SET term_months = 360 WHERE tenant_id = $1", other.tenant_id
    )
    assert result == "UPDATE 0"
    # Re-homing an own row into another tenant violates the WITH CHECK clause.
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await app_conn.execute("UPDATE cases SET tenant_id = $1", other.tenant_id)


async def test_runtime_role_cannot_disable_rls(
    app_conn: asyncpg.Connection, populated: Firm
) -> None:
    await app_conn.execute("SET row_security = off")
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await app_conn.fetchval("SELECT count(*) FROM cases")


async def test_runtime_role_owns_nothing_and_cannot_bypass(app_conn: asyncpg.Connection) -> None:
    role = await app_conn.fetchrow(
        "SELECT rolsuper, rolbypassrls, rolcreaterole FROM pg_roles WHERE rolname = current_user"
    )
    assert role is not None
    assert tuple(role) == (False, False, False)
    owned = await app_conn.fetchval(
        "SELECT count(*) FROM pg_tables WHERE schemaname = 'public' AND tableowner = current_user"
    )
    assert owned == 0


async def test_runtime_role_cannot_run_ddl_or_hard_delete_cases(
    app_conn: asyncpg.Connection, populated: Firm
) -> None:
    await _set_tenant(app_conn, populated.tenant_id)
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await app_conn.execute("DELETE FROM cases")
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await app_conn.execute("ALTER TABLE cases DISABLE ROW LEVEL SECURITY")
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await app_conn.execute(
            "INSERT INTO tenants (id, name, status, created_at, updated_at) "
            "VALUES (gen_random_uuid(), 'x', 'active', now(), now())"
        )
