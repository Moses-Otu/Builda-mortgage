"""Acceptance criterion 6: one audit event per mutation; the log is immutable."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

import asyncpg
import httpx
import pytest
from fastapi import FastAPI

from tests.conftest import TEST_DB, CountAudit, Firm, db_url

# Every mutating route, mapped to the action it must record. If a new mutating route is
# added without a test here, test_every_mutating_route_is_covered fails.
AUDITED_ROUTES = {
    ("POST", "/api/v1/members"): "member.added",
    ("PATCH", "/api/v1/members/{membership_id}"): "member.updated",
    ("POST", "/api/v1/me/invitations/{membership_id}/accept"): "member.invitation_accepted",
    ("POST", "/api/v1/cases"): "case.created",
    ("PATCH", "/api/v1/cases/{case_id}"): "case.updated",
    ("POST", "/api/v1/cases/{case_id}/transitions"): "case.status_changed",
    ("POST", "/api/v1/cases/{case_id}/applicants"): "applicant.added",
    ("PATCH", "/api/v1/cases/{case_id}/applicants/{applicant_id}"): "applicant.updated",
    ("DELETE", "/api/v1/cases/{case_id}/applicants/{applicant_id}"): "applicant.removed",
}


def test_every_mutating_route_is_covered(app: FastAPI) -> None:
    mutating = {
        (method.upper(), path)
        for path, operations in app.openapi()["paths"].items()
        for method in operations
        if method.upper() in {"POST", "PUT", "PATCH", "DELETE"}
    }
    assert mutating == set(AUDITED_ROUTES)


Action = Callable[[Firm, dict[str, Any]], Awaitable[httpx.Response]]


async def _setup(firm: Firm) -> dict[str, Any]:
    case = await firm.create_case()
    applicant = await firm.add_applicant(case["id"])
    subject = await firm.add_member("adviser")
    invitee = await firm.add_member("read_only", accept=False)
    return {
        "case": case,
        "applicant": applicant,
        "member_id": firm.members[subject],
        "invitee": invitee,
        "invitation_id": firm.members[invitee],
    }


ACTIONS: dict[str, Action] = {
    "member.added": lambda f, s: f.request(
        "POST", "/api/v1/members", json={"idp_subject": "new-user", "role": "read_only"}
    ),
    "member.updated": lambda f, s: f.request(
        "PATCH", f"/api/v1/members/{s['member_id']}", json={"version": 2, "role": "read_only"}
    ),
    "member.invitation_accepted": lambda f, s: f.accept(s["invitee"], s["invitation_id"]),
    "case.created": lambda f, s: f.request("POST", "/api/v1/cases", json={"purpose": "purchase"}),
    "case.updated": lambda f, s: f.request(
        "PATCH", f"/api/v1/cases/{s['case']['id']}", json={"version": 1, "term_months": 200}
    ),
    "case.status_changed": lambda f, s: f.request(
        "POST",
        f"/api/v1/cases/{s['case']['id']}/transitions",
        json={"to_status": "collecting_evidence", "version": 1},
    ),
    "applicant.added": lambda f, s: f.request(
        "POST",
        f"/api/v1/cases/{s['case']['id']}/applicants",
        json={"first_name": "B", "last_name": "C"},
    ),
    "applicant.updated": lambda f, s: f.request(
        "PATCH",
        f"/api/v1/cases/{s['case']['id']}/applicants/{s['applicant']['id']}",
        json={"version": 1, "first_name": "Changed"},
    ),
    "applicant.removed": lambda f, s: f.request(
        "DELETE",
        f"/api/v1/cases/{s['case']['id']}/applicants/{s['applicant']['id']}",
        params={"version": 1},
    ),
}


@pytest.mark.parametrize("action", sorted(set(AUDITED_ROUTES.values())))
async def test_mutation_writes_exactly_one_event(
    firm: Firm, audit_count: CountAudit, action: str
) -> None:
    state = await _setup(firm)
    before = await audit_count(firm.tenant_id)
    resp = await ACTIONS[action](firm, state)
    assert resp.status_code in (200, 201, 204), resp.text
    assert await audit_count(firm.tenant_id) == before + 1


async def test_failed_mutation_writes_no_event(firm: Firm, audit_count: CountAudit) -> None:
    case = await firm.create_case()
    before = await audit_count(firm.tenant_id)
    resp = await firm.request(
        "PATCH", f"/api/v1/cases/{case['id']}", json={"version": 99, "term_months": 200}
    )
    assert resp.status_code == 409
    assert await audit_count(firm.tenant_id) == before


async def test_case_history_is_ordered_attributed_and_redacted(firm: Firm) -> None:
    case = await firm.create_case(loan_amount={"amount_minor": 100, "currency": "GBP"})
    applicant = await firm.add_applicant(case["id"], first_name="Distinctive", last_name="Surname")
    await firm.request(
        "PATCH",
        f"/api/v1/cases/{case['id']}/applicants/{applicant['id']}",
        json={"version": 1, "first_name": "Different"},
        headers={"X-Request-ID": "trace-123"},
    )
    resp = await firm.request("GET", f"/api/v1/cases/{case['id']}/events")
    assert resp.status_code == 200
    events = resp.json()["items"]
    assert [e["action"] for e in events] == ["case.created", "applicant.added", "applicant.updated"]
    assert all(e["actor_type"] == "user" and e["actor_user_id"] for e in events)
    assert events[0]["changes"]["loan_amount_minor"] == {"from": None, "to": 100}
    assert events[2]["changes"] == {"first_name": {"changed": True}}
    assert events[2]["request_id"] == "trace-123"
    assert "Distinctive" not in resp.text
    assert "Surname" not in resp.text


async def test_close_reason_recorded(firm: Firm) -> None:
    case = await firm.create_case()
    await firm.request(
        "POST",
        f"/api/v1/cases/{case['id']}/transitions",
        json={"to_status": "closed", "version": 1, "reason": "Client withdrew"},
    )
    events = (await firm.request("GET", f"/api/v1/cases/{case['id']}/events")).json()["items"]
    assert events[-1]["changes"] == {
        "status": {"from": "draft", "to": "closed"},
        "reason": "Client withdrew",
    }


async def test_invitation_acceptance_is_attributed_to_the_invitee(firm: Firm) -> None:
    invitee = await firm.add_member("adviser", accept=False)
    await firm.accept(invitee, firm.members[invitee])
    events = await _tenant_events(firm)
    accepted = [e for e in events if e["action"] == "member.invitation_accepted"]
    assert len(accepted) == 1
    assert accepted[0]["entity_id"] == firm.members[invitee]
    assert accepted[0]["actor_type"] == "user"
    assert accepted[0]["actor_user_id"] is not None
    assert accepted[0]["changes"] == {"status": {"from": "invited", "to": "active"}}


async def test_onboarding_is_attributed_to_the_operator(firm: Firm) -> None:
    events = await _tenant_events(firm)
    created = [e for e in events if e["action"] == "tenant.created"]
    assert len(created) == 1
    assert created[0]["actor_type"] == "operator"
    assert created[0]["actor_ref"] == "operator:pytest"
    assert created[0]["actor_user_id"] is None


async def test_actor_fields_must_be_consistent(firm: Firm) -> None:
    conn = await asyncpg.connect(db_url("meip_app", TEST_DB, driver="postgresql"))
    try:
        await conn.execute("SELECT set_config('app.tenant_id', $1, false)", str(firm.tenant_id))
        user_id = await conn.fetchval("SELECT id FROM users LIMIT 1")
        invalid = [
            ("user", None, None),  # a user event must name the user
            ("user", user_id, "operator:x"),  # ...and nothing else
            ("operator", None, None),  # an operator event must name the operator
            ("operator", user_id, "operator:x"),  # ...and no user
            ("system", user_id, None),  # a system event is not a user's action
        ]
        for actor_type, actor_user_id, actor_ref in invalid:
            with pytest.raises(asyncpg.CheckViolationError):
                await conn.execute(
                    "INSERT INTO audit_events (id, tenant_id, occurred_at, actor_type,"
                    " actor_user_id, actor_ref, action, entity_type, entity_id, changes)"
                    " VALUES (gen_random_uuid(), $1, now(), $2, $3, $4, 'x', 'x',"
                    " gen_random_uuid(), '{}')",
                    firm.tenant_id,
                    actor_type,
                    actor_user_id,
                    actor_ref,
                )
    finally:
        await conn.close()


async def _tenant_events(firm: Firm) -> list[dict[str, Any]]:
    conn = await asyncpg.connect(db_url("meip_app", TEST_DB, driver="postgresql"))
    try:
        await conn.execute("SELECT set_config('app.tenant_id', $1, false)", str(firm.tenant_id))
        rows = await conn.fetch(
            "SELECT action, entity_id, actor_type, actor_user_id, actor_ref, changes::text"
            " AS changes FROM audit_events ORDER BY occurred_at, id"
        )
    finally:
        await conn.close()
    return [{**dict(r), "changes": json.loads(r["changes"])} for r in rows]


@pytest.mark.parametrize("role", ["meip_owner", "meip_app"])
@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE audit_events SET action = 'tampered'",
        "DELETE FROM audit_events",
        "TRUNCATE audit_events",
    ],
)
async def test_audit_log_cannot_be_modified(firm: Firm, role: str, statement: str) -> None:
    await firm.create_case()
    conn = await asyncpg.connect(db_url(role, TEST_DB, driver="postgresql"))
    try:
        await conn.execute("SELECT set_config('app.tenant_id', $1, false)", str(firm.tenant_id))
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute(statement)
    finally:
        await conn.close()
