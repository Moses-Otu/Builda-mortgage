"""Concurrency: invariants must hold when requests race.

Requests run concurrently through the ASGI app on separate pooled database connections.
Each test asserts an outcome that must hold regardless of how the race interleaves.
"""

from __future__ import annotations

import asyncio
import uuid

import asyncpg
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from meip.tenancy.models import Membership
from tests.conftest import TEST_DB, Firm, db_url


async def _active_admins(
    owner_sessionmaker: async_sessionmaker[AsyncSession], tenant_id: uuid.UUID
) -> int:
    async with owner_sessionmaker() as session:
        n = await session.scalar(
            select(func.count()).where(
                Membership.tenant_id == tenant_id,
                Membership.role == "firm_admin",
                Membership.status == "active",
            )
        )
    return int(n or 0)


async def test_concurrent_admin_demotions_keep_one_admin(
    firm: Firm, owner_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    second = await firm.add_member("firm_admin")
    first_id, second_id = firm.admin_membership_id, firm.members[second]

    # Each admin demotes the other at the same moment.
    results = await asyncio.gather(
        firm.request(
            "PATCH", f"/api/v1/members/{second_id}", json={"version": 2, "role": "adviser"}
        ),
        firm.request(
            "PATCH",
            f"/api/v1/members/{first_id}",
            as_=second,
            json={"version": 1, "role": "adviser"},
        ),
    )
    statuses = sorted(r.status_code for r in results)
    assert statuses in ([200, 403], [200, 409], [200, 422]), [r.text for r in results]
    assert await _active_admins(owner_sessionmaker, firm.tenant_id) == 1


async def test_many_concurrent_admin_deactivations_keep_one_admin(
    firm: Firm, owner_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    others = [await firm.add_member("firm_admin") for _ in range(3)]
    targets = [(firm.admin_membership_id, 1)] + [(firm.members[s], 2) for s in others]

    results = await asyncio.gather(
        *(
            firm.request(
                "PATCH",
                f"/api/v1/members/{mid}",
                json={"version": version, "status": "deactivated"},
            )
            for mid, version in targets
        )
    )
    assert all(r.status_code < 500 for r in results), [r.text for r in results]
    assert await _active_admins(owner_sessionmaker, firm.tenant_id) >= 1


async def test_concurrent_duplicate_invitations_conflict_cleanly(firm: Firm) -> None:
    body = {"idp_subject": f"dup-{uuid.uuid4()}", "role": "adviser"}
    results = await asyncio.gather(
        *(firm.request("POST", "/api/v1/members", json=body) for _ in range(4))
    )
    statuses = sorted(r.status_code for r in results)
    assert statuses == [201, 409, 409, 409], [r.text for r in results]


async def test_concurrent_updates_with_same_version_only_one_wins(firm: Firm) -> None:
    case = await firm.create_case()
    results = await asyncio.gather(
        *(
            firm.request(
                "PATCH", f"/api/v1/cases/{case['id']}", json={"version": 1, "term_months": t}
            )
            for t in (120, 240, 360)
        )
    )
    statuses = sorted(r.status_code for r in results)
    assert statuses == [200, 409, 409], [r.text for r in results]


async def test_concurrent_applicant_adds_get_distinct_positions(firm: Firm) -> None:
    case = await firm.create_case()
    results = await asyncio.gather(
        *(
            firm.request(
                "POST",
                f"/api/v1/cases/{case['id']}/applicants",
                json={"first_name": f"A{i}", "last_name": "B"},
            )
            for i in range(6)
        )
    )
    created = [r.json()["position"] for r in results if r.status_code == 201]
    rejected = [r for r in results if r.status_code != 201]
    assert sorted(created) == [1, 2, 3, 4]
    assert all(r.status_code == 422 for r in rejected), [r.text for r in rejected]


async def test_applicant_update_waits_for_and_respects_case_lock(firm: Firm) -> None:
    """update_applicant must lock the case, so a concurrent move out of an editable status
    cannot be overtaken by an applicant edit that checked editability too early."""
    case = await firm.create_case()
    applicant = await firm.add_applicant(case["id"])

    conn = await asyncpg.connect(db_url("meip_app", TEST_DB, driver="postgresql"))
    try:
        transaction = conn.transaction()
        await transaction.start()
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(firm.tenant_id))
        await conn.execute("SELECT 1 FROM cases WHERE id = $1 FOR UPDATE", uuid.UUID(case["id"]))

        edit = asyncio.create_task(
            firm.request(
                "PATCH",
                f"/api/v1/cases/{case['id']}/applicants/{applicant['id']}",
                json={"version": 1, "first_name": "Late"},
            )
        )
        await asyncio.sleep(0.5)
        assert not edit.done(), "applicant update did not wait for the case lock"

        # The competing transaction closes the case while holding the lock.
        await conn.execute(
            "UPDATE cases SET status = 'closed', version = version + 1 WHERE id = $1",
            uuid.UUID(case["id"]),
        )
        await transaction.commit()
    finally:
        await conn.close()

    resp = await asyncio.wait_for(edit, timeout=10)
    assert resp.status_code == 409, resp.text
    current = await firm.request("GET", f"/api/v1/cases/{case['id']}/applicants/{applicant['id']}")
    assert current.json()["first_name"] == "Alex"
