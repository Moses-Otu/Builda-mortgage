"""Relationship constraints that keep tenants apart even when RLS is not in play.

These tests write as the *owner* role, which is not subject to RLS, to prove that the
composite foreign keys alone stop a row in one firm from referencing a row in another.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import asyncpg
import pytest

from tests.conftest import TEST_DB, Firm, MakeFirm, db_url


@pytest.fixture
async def owner_conn() -> AsyncIterator[asyncpg.Connection]:
    conn = await asyncpg.connect(db_url("meip_owner", TEST_DB, driver="postgresql"))
    try:
        yield conn
    finally:
        await conn.close()


async def test_applicant_cannot_reference_another_firms_case(
    owner_conn: asyncpg.Connection, make_firm: MakeFirm
) -> None:
    firm_a, firm_b = await make_firm("Firm A"), await make_firm("Firm B")
    case_b = await firm_b.create_case()
    with pytest.raises(asyncpg.ForeignKeyViolationError, match="fk_applicants_case"):
        await owner_conn.execute(
            "INSERT INTO applicants (id, tenant_id, case_id, position, first_name, last_name,"
            " created_at, updated_at, version)"
            " VALUES ($1, $2, $3, 1, 'X', 'Y', now(), now(), 1)",
            uuid.uuid4(),
            firm_a.tenant_id,
            uuid.UUID(case_b["id"]),
        )


async def test_applicant_cannot_be_moved_to_another_firm(
    owner_conn: asyncpg.Connection, make_firm: MakeFirm
) -> None:
    firm_a, firm_b = await make_firm("Firm A"), await make_firm("Firm B")
    case_a = await firm_a.create_case()
    applicant = await firm_a.add_applicant(case_a["id"])
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await owner_conn.execute(
            "UPDATE applicants SET tenant_id = $1 WHERE id = $2",
            firm_b.tenant_id,
            uuid.UUID(applicant["id"]),
        )


async def test_case_adviser_cannot_belong_to_another_firm(
    owner_conn: asyncpg.Connection, make_firm: MakeFirm
) -> None:
    firm_a, firm_b = await make_firm("Firm A"), await make_firm("Firm B")
    case_a = await firm_a.create_case()
    with pytest.raises(asyncpg.ForeignKeyViolationError, match="fk_cases_adviser_membership"):
        await owner_conn.execute(
            "UPDATE cases SET adviser_membership_id = $1 WHERE id = $2",
            firm_b.admin_membership_id,
            uuid.UUID(case_a["id"]),
        )


async def test_applicants_expose_tenant_scoped_key_for_future_references(
    owner_conn: asyncpg.Connection,
) -> None:
    columns = await owner_conn.fetchval(
        "SELECT array_agg(a.attname ORDER BY k.ord) FROM pg_constraint c "
        "CROSS JOIN LATERAL unnest(c.conkey) WITH ORDINALITY AS k(attnum, ord) "
        "JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum "
        "WHERE c.conname = 'uq_applicants_tenant_id_id'"
    )
    assert columns == ["tenant_id", "id"]


async def test_api_rejects_another_firms_membership_as_adviser(
    firm: Firm, make_firm: MakeFirm
) -> None:
    other = await make_firm("Other Firm")
    resp = await firm.request(
        "POST",
        "/api/v1/cases",
        json={"purpose": "purchase", "adviser_membership_id": str(other.admin_membership_id)},
    )
    assert resp.status_code == 422
    assert str(other.admin_membership_id) not in resp.text
