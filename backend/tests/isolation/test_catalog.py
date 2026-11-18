"""Catalog guard: verifies the isolation configuration directly from PostgreSQL's catalogs.

Behavioural RLS tests only cover tables someone remembered to list. These tests read the
schema itself, so a new table, a loosened policy, an extra grant or an unsafe definer
function fails the build even if no behavioural test was written for it.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator

import asyncpg
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.conftest import TEST_DB, db_url
from tests.isolation.schema_policy import (
    COLUMN_INSERTS,
    COLUMN_UPDATES,
    EXEMPT,
    EXPECTED_TRIGGERS,
    EXPECTED_USERS_COLUMNS,
    REFERENCE,
    SPECIAL_POLICY,
    STANDARD_EXPRESSION,
    STANDARD_POLICY,
    TENANT_SCOPED,
)

APP_ROLE = "meip_app"
OWNER_ROLE = "meip_owner"


def _normalise(expression: str | None) -> str | None:
    return None if expression is None else re.sub(r"\s+", " ", expression).strip()


@pytest.fixture(scope="module")
async def catalog(owner_engine: AsyncEngine) -> AsyncIterator[asyncpg.Connection]:
    # owner_engine guarantees the schema is migrated before the catalog is inspected.
    conn = await asyncpg.connect(db_url(OWNER_ROLE, TEST_DB, driver="postgresql"))
    try:
        yield conn
    finally:
        await conn.close()


async def _public_tables(conn: asyncpg.Connection) -> set[str]:
    rows = await conn.fetch(
        "SELECT relname FROM pg_class WHERE relnamespace = 'public'::regnamespace "
        "AND relkind IN ('r', 'p')"
    )
    return {r["relname"] for r in rows}


async def _app_privileges(conn: asyncpg.Connection, table: str) -> set[str]:
    rows = await conn.fetch(
        "SELECT privilege_type FROM information_schema.role_table_grants "
        "WHERE grantee = $1 AND table_schema = 'public' AND table_name = $2",
        APP_ROLE,
        table,
    )
    return {r["privilege_type"] for r in rows}


async def _policies(conn: asyncpg.Connection, table: str) -> list[asyncpg.Record]:
    return await conn.fetch(
        "SELECT policyname, cmd, roles::text[] AS roles, permissive, qual, with_check "
        "FROM pg_policies WHERE schemaname = 'public' AND tablename = $1",
        table,
    )


async def test_every_table_is_classified(catalog: asyncpg.Connection) -> None:
    groups = [set(TENANT_SCOPED), set(SPECIAL_POLICY), set(EXEMPT), set(REFERENCE)]
    classified = set().union(*groups)
    overlap = {t for i, a in enumerate(groups) for b in groups[i + 1 :] for t in a & b}
    assert not overlap, f"Tables classified twice: {overlap}"
    actual = await _public_tables(catalog)
    assert actual - classified == set(), (
        f"Unclassified tables {actual - classified}: add them to tests/isolation/schema_policy.py"
    )
    assert classified - actual == set(), f"Classified tables missing: {classified - actual}"


@pytest.mark.parametrize("table", sorted(TENANT_SCOPED))
async def test_tenant_scoped_table_is_isolated(catalog: asyncpg.Connection, table: str) -> None:
    rls = await catalog.fetchrow(
        "SELECT relrowsecurity FROM pg_class WHERE oid = $1::regclass", f"public.{table}"
    )
    assert rls is not None
    assert rls["relrowsecurity"], f"RLS is not enabled on {table}"

    column = await catalog.fetchrow(
        "SELECT data_type, is_nullable FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = $1 AND column_name = 'tenant_id'",
        table,
    )
    assert column is not None, f"{table} has no tenant_id column"
    assert (column["data_type"], column["is_nullable"]) == ("uuid", "NO")

    policies = await _policies(catalog, table)
    assert len(policies) == 1, f"{table} must have exactly one policy, found {len(policies)}"
    policy = policies[0]
    assert policy["policyname"] == STANDARD_POLICY
    assert policy["cmd"] == "ALL"
    assert policy["permissive"] == "PERMISSIVE"
    assert list(policy["roles"]) == ["public"]
    assert _normalise(policy["qual"]) == STANDARD_EXPRESSION
    assert _normalise(policy["with_check"]) == STANDARD_EXPRESSION

    assert await _app_privileges(catalog, table) == set(TENANT_SCOPED[table])


@pytest.mark.parametrize("table", sorted(SPECIAL_POLICY))
async def test_special_policy_table_is_isolated(catalog: asyncpg.Connection, table: str) -> None:
    expected, privileges = SPECIAL_POLICY[table]
    rls = await catalog.fetchval(
        "SELECT relrowsecurity FROM pg_class WHERE oid = $1::regclass", f"public.{table}"
    )
    assert rls, f"RLS is not enabled on {table}"
    policies = await _policies(catalog, table)
    assert len(policies) == 1
    policy = policies[0]
    assert policy["policyname"] == expected.name
    assert policy["cmd"] == "ALL"
    assert policy["permissive"] == "PERMISSIVE"
    assert _normalise(policy["qual"]) == expected.using
    assert _normalise(policy["with_check"]) == expected.with_check
    assert await _app_privileges(catalog, table) == set(privileges)


@pytest.mark.parametrize("table", sorted(REFERENCE))
async def test_reference_table_is_read_only_and_tenant_free(
    catalog: asyncpg.Connection, table: str
) -> None:
    columns = await catalog.fetch(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = $1",
        table,
    )
    assert "tenant_id" not in {c["column_name"] for c in columns}
    assert await _app_privileges(catalog, table) == {"SELECT"}
    column_grants = await catalog.fetchval(
        "SELECT count(*) FROM information_schema.column_privileges "
        "WHERE grantee = $1 AND table_name = $2 AND privilege_type <> 'SELECT'",
        APP_ROLE,
        table,
    )
    assert column_grants == 0


async def test_column_level_grants_are_exactly_as_expected(catalog: asyncpg.Connection) -> None:
    rows = await catalog.fetch(
        "SELECT table_name, column_name FROM information_schema.column_privileges "
        "WHERE grantee = $1 AND table_schema = 'public' AND privilege_type = 'UPDATE'",
        APP_ROLE,
    )
    granted: dict[str, set[str]] = {}
    for row in rows:
        granted.setdefault(row["table_name"], set()).add(row["column_name"])
    # Whole-table UPDATE also shows every column here; only check tables without it.
    column_only = {
        t: cols for t, cols in granted.items() if "UPDATE" not in await _app_privileges(catalog, t)
    }
    assert column_only == {t: set(c) for t, c in COLUMN_UPDATES.items()}


@pytest.mark.parametrize("table", sorted(EXEMPT))
async def test_exempt_table_is_inaccessible_to_runtime_role(
    catalog: asyncpg.Connection, table: str
) -> None:
    assert await _app_privileges(catalog, table) == set()


async def test_users_table_holds_identity_only(catalog: asyncpg.Connection) -> None:
    rows = await catalog.fetch(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = 'users'"
    )
    assert {r["column_name"] for r in rows} == EXPECTED_USERS_COLUMNS


async def test_runtime_role_cannot_bypass_isolation(catalog: asyncpg.Connection) -> None:
    role = await catalog.fetchrow(
        "SELECT rolsuper, rolbypassrls, rolcreaterole, rolcreatedb, rolreplication "
        "FROM pg_roles WHERE rolname = $1",
        APP_ROLE,
    )
    assert role is not None
    assert not any(role.values()), dict(role)

    # Not a member of (and so cannot SET ROLE to) any role that owns objects or bypasses RLS.
    privileged_memberships = await catalog.fetch(
        "SELECT r.rolname FROM pg_roles r "
        "WHERE r.rolname <> $1 AND pg_has_role($1, r.oid, 'MEMBER') "
        "AND (r.rolsuper OR r.rolbypassrls OR r.rolname = $2)",
        APP_ROLE,
        OWNER_ROLE,
    )
    assert privileged_memberships == []

    owned = await catalog.fetchval(
        "SELECT count(*) FROM pg_class c JOIN pg_roles r ON r.oid = c.relowner "
        "WHERE r.rolname = $1",
        APP_ROLE,
    )
    assert owned == 0
    owned_functions = await catalog.fetchval(
        "SELECT count(*) FROM pg_proc p JOIN pg_roles r ON r.oid = p.proowner WHERE r.rolname = $1",
        APP_ROLE,
    )
    assert owned_functions == 0


async def test_runtime_role_cannot_create_objects(catalog: asyncpg.Connection) -> None:
    can_create_in_public = await catalog.fetchval(
        "SELECT has_schema_privilege($1, 'public', 'CREATE')", APP_ROLE
    )
    can_create_temp = await catalog.fetchval(
        "SELECT has_database_privilege($1, current_database(), 'TEMPORARY')", APP_ROLE
    )
    assert not can_create_in_public
    assert not can_create_temp


async def _definer_functions(conn: asyncpg.Connection) -> list[asyncpg.Record]:
    return await conn.fetch(
        "SELECT p.oid, p.proname, p.proconfig, pg_get_userbyid(p.proowner) AS owner, "
        "p.proacl IS NULL AS default_acl "
        "FROM pg_proc p WHERE p.pronamespace = 'public'::regnamespace AND p.prosecdef"
    )


async def test_security_definer_functions_are_locked_down(catalog: asyncpg.Connection) -> None:
    functions = await _definer_functions(catalog)
    assert functions, "expected at least the Phase 1 definer functions"
    for fn in functions:
        name = fn["proname"]
        config = list(fn["proconfig"] or [])
        search_paths = [c for c in config if c.startswith("search_path=")]
        assert search_paths, f"{name} has no fixed search_path"
        path = [p.strip() for p in search_paths[0].removeprefix("search_path=").split(",")]
        assert path[-1] == "pg_temp", f"{name}: pg_temp must be last in search_path, got {path}"
        assert fn["owner"] == OWNER_ROLE, f"{name} is owned by {fn['owner']}"
        # A NULL ACL means the default, which grants EXECUTE to PUBLIC.
        assert not fn["default_acl"], f"{name} has default (PUBLIC) execute privileges"
        public_execute = await catalog.fetchval(
            "SELECT count(*) FROM pg_proc p, aclexplode(p.proacl) a "
            "WHERE p.oid = $1 AND a.grantee = 0 AND a.privilege_type = 'EXECUTE'",
            fn["oid"],
        )
        assert public_execute == 0, f"{name} is executable by PUBLIC"
        assert await catalog.fetchval(
            "SELECT has_function_privilege($1, $2::oid, 'EXECUTE')", APP_ROLE, fn["oid"]
        ), f"{name} is not executable by the runtime role"


async def test_register_user_returns_only_an_identifier(catalog: asyncpg.Connection) -> None:
    result_type = await catalog.fetchval(
        "SELECT format_type(prorettype, NULL) FROM pg_proc WHERE proname = 'app_register_user'"
    )
    assert result_type == "uuid"


async def test_constraint_names_follow_the_naming_convention(catalog: asyncpg.Connection) -> None:
    """Every CHECK is named ck_<table>_<name> exactly once (0001 produced doubled prefixes)."""
    rows = await catalog.fetch(
        "SELECT conrelid::regclass::text AS tbl, conname FROM pg_constraint "
        "WHERE connamespace = 'public'::regnamespace AND contype = 'c'"
    )
    assert rows
    for row in rows:
        table, name = row["tbl"], row["conname"]
        assert name.startswith(f"ck_{table}_"), f"{name} on {table}"
        assert f"ck_{table}_ck_" not in name, f"doubled prefix: {name}"


async def test_column_level_inserts_are_exactly_as_expected(catalog: asyncpg.Connection) -> None:
    for table, columns in COLUMN_INSERTS.items():
        assert "INSERT" not in await _app_privileges(catalog, table), table
        rows = await catalog.fetch(
            "SELECT column_name FROM information_schema.column_privileges "
            "WHERE grantee = $1 AND table_name = $2 AND privilege_type = 'INSERT'",
            APP_ROLE,
            table,
        )
        assert {r["column_name"] for r in rows} == set(columns), table


async def test_integrity_triggers_exist_and_are_enabled(catalog: asyncpg.Connection) -> None:
    rows = await catalog.fetch(
        "SELECT tgname, tgrelid::regclass::text AS tbl, tgenabled::text AS enabled "
        "FROM pg_trigger WHERE NOT tgisinternal"
    )
    found = {r["tgname"]: (r["tbl"], r["enabled"]) for r in rows}
    for name, table in EXPECTED_TRIGGERS.items():
        assert name in found, f"missing trigger {name}"
        assert found[name] == (table, "O"), f"{name}: {found[name]}"


async def test_no_definer_function_writes_audit_details(catalog: asyncpg.Connection) -> None:
    offenders = await catalog.fetch(
        "SELECT proname FROM pg_proc WHERE pronamespace = 'public'::regnamespace "
        "AND prosecdef AND prosrc ILIKE '%audit_event_details%'"
    )
    assert offenders == []


async def test_runtime_role_cannot_disable_triggers(catalog: asyncpg.Connection) -> None:
    assert not await catalog.fetchval(
        "SELECT has_parameter_privilege($1, 'session_replication_role', 'SET')", APP_ROLE
    )
