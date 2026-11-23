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

Route = tuple[str, str]
Action = Callable[[Firm, dict[str, Any]], Awaitable[httpx.Response]]

_C = "/api/v1/cases/{case_id}"
_F = _C + "/financial"

# Every mutating route, mapped to the action it must record. If a new mutating route is
# added without an entry here, test_every_mutating_route_is_covered fails.
AUDITED_ROUTES: dict[Route, str] = {
    ("POST", "/api/v1/members"): "member.added",
    ("PATCH", "/api/v1/members/{membership_id}"): "member.updated",
    ("POST", "/api/v1/me/invitations/{membership_id}/accept"): "member.invitation_accepted",
    ("PATCH", "/api/v1/firm/settings"): "firm_settings.updated",
    ("POST", "/api/v1/cases"): "case.created",
    ("PATCH", _C): "case.updated",
    ("POST", _C + "/transitions"): "case.status_changed",
    ("POST", _C + "/applicants"): "applicant.added",
    ("PATCH", _C + "/applicants/{applicant_id}"): "applicant.updated",
    ("POST", _C + "/applicants/{applicant_id}/removal"): "applicant.removed",
    ("POST", _C + "/applicants/{applicant_id}/reinstatement"): "applicant.reinstated",
    ("POST", _F + "/income-sources"): "financial_item.added",
    ("PATCH", _F + "/income-sources/{income_source_id}"): "financial_item.updated",
    ("POST", _F + "/commitments"): "financial_item.added",
    ("PATCH", _F + "/commitments/{commitment_id}"): "financial_item.updated",
    ("POST", _F + "/expenditure"): "financial_item.added",
    ("PATCH", _F + "/expenditure/{expenditure_item_id}"): "financial_item.updated",
    ("POST", _F + "/income-sources/{income_source_id}/withdrawal"): ("financial_item.withdrawn"),
    ("POST", _F + "/commitments/{commitment_id}/withdrawal"): "financial_item.withdrawn",
    ("POST", _F + "/expenditure/{expenditure_item_id}/withdrawal"): "financial_item.withdrawn",
    ("POST", "/api/v1/cases/{case_id}/documents"): "document.uploaded",
    ("POST", "/api/v1/cases/{case_id}/documents/{document_id}/review"): "document.reviewed",
    ("POST", _F + "/observations"): "observation.recorded",
    ("POST", _F + "/observations/{observation_id}/retirement"): "observation.retired",
    ("POST", _C + "/checks/{finding_key}/acknowledgement"): "check.acknowledged",
    ("POST", _C + "/bank-accounts/{account_id}/holders"): "bank_account.holder_added",
    ("POST", _C + "/profile/approvals"): "profile.approved",
}


# Mutating routes outside this firm-audit coverage, each with the reason.
NOT_FIRM_AUDITED: dict[Route, str] = {
    # A person's own sign-in and password: no firm's data changes. Outcomes go to the
    # operational log without emails (test_password_auth.py).
    ("POST", "/api/v1/auth/login"): "account sign-in, not firm data",
    ("POST", "/api/v1/auth/password"): "account password, not firm data",
    # Password mode only (404 in this suite's dev mode); its audit event is asserted in
    # test_password_auth.py.
    ("POST", "/api/v1/members/{membership_id}/password-reset"): "password mode only",
    # Agentic assessment: these need a finished assessment or an open request, or start the
    # agent, whose own system-actor events follow. Their audit events are asserted in
    # test_assessment.py (started, completed, evidence requested, decided, request closed).
    ("POST", _C + "/assessments"): "assessment.started, then the agent's own events",
    ("POST", _C + "/assessments/{assessment_id}/decisions"): "assessment.decided (test_assessment)",
    ("POST", _C + "/evidence-requests/{request_id}/withdrawal"): "evidence.request_closed",
    # Read-only: the deterministic calculator changes nothing.
    ("POST", _C + "/affordability/scenarios"): "read-only calculation",
}


def test_every_mutating_route_is_covered(app: FastAPI) -> None:
    mutating = {
        (method.upper(), path)
        for path, operations in app.openapi()["paths"].items()
        for method in operations
        if method.upper() in {"POST", "PUT", "PATCH", "DELETE"}
    }
    assert mutating == set(AUDITED_ROUTES) | set(NOT_FIRM_AUDITED)


async def _setup(firm: Firm) -> dict[str, Any]:
    case = await firm.create_case()
    cid = case["id"]
    applicant = await firm.add_applicant(cid)
    second = await firm.add_applicant(cid)
    gone = await firm.add_applicant(cid)
    removed = await firm.request(
        "POST",
        f"/api/v1/cases/{cid}/applicants/{gone['id']}/removal",
        if_match=1,
        json={"removal_reason": "added_in_error"},
    )
    base = f"/api/v1/cases/{cid}/financial"
    source = await firm.request(
        "POST",
        f"{base}/income-sources",
        json={"applicant_id": applicant["id"], "kind": "employment"},
    )
    commitment = await firm.request(
        "POST",
        f"{base}/commitments",
        json={"kind": "credit_card", "parties": [{"applicant_id": applicant["id"]}]},
    )
    spend = await firm.request(
        "POST", f"{base}/expenditure", json={"category": "utilities", "scope": "household"}
    )
    observation = await firm.request(
        "POST",
        f"{base}/observations",
        json={
            "income_source_id": source.json()["id"],
            "measure": "basic_pay",
            "value": {"type": "money", "amount_minor": 300_000},
            "frequency": "monthly",
        },
    )
    subject = await firm.add_member("adviser")
    invitee = await firm.add_member("read_only", accept=False)
    return {
        "case": case,
        "base": base,
        "applicant": applicant,
        "second": second,
        "removed": removed.json(),
        "source": source.json(),
        "commitment": commitment.json(),
        "spend": spend.json(),
        "observation": observation.json(),
        "member_id": firm.members[subject],
        "invitee": invitee,
        "invitation_id": firm.members[invitee],
    }


def _case(s: dict[str, Any]) -> str:
    return f"/api/v1/cases/{s['case']['id']}"


ACTIONS: dict[Route, Action] = {
    ("POST", "/api/v1/members"): lambda f, s: f.request(
        "POST", "/api/v1/members", json={"idp_subject": "new-user", "role": "read_only"}
    ),
    ("PATCH", "/api/v1/members/{membership_id}"): lambda f, s: f.request(
        "PATCH", f"/api/v1/members/{s['member_id']}", if_match=2, json={"role": "read_only"}
    ),
    ("POST", "/api/v1/me/invitations/{membership_id}/accept"): lambda f, s: f.accept(
        s["invitee"], s["invitation_id"]
    ),
    ("PATCH", "/api/v1/firm/settings"): lambda f, s: f.request(
        "PATCH", "/api/v1/firm/settings", if_match=1, json={"max_applicants": 6}
    ),
    ("POST", "/api/v1/cases"): lambda f, s: f.request(
        "POST", "/api/v1/cases", json={"purpose": "purchase"}
    ),
    ("PATCH", _C): lambda f, s: f.request("PATCH", _case(s), if_match=1, json={"term_months": 200}),
    ("POST", _C + "/transitions"): lambda f, s: f.request(
        "POST", f"{_case(s)}/transitions", if_match=1, json={"to_status": "collecting_evidence"}
    ),
    ("POST", _C + "/applicants"): lambda f, s: f.request(
        "POST", f"{_case(s)}/applicants", json={"first_name": "B", "last_name": "C"}
    ),
    ("PATCH", _C + "/applicants/{applicant_id}"): lambda f, s: f.request(
        "PATCH",
        f"{_case(s)}/applicants/{s['applicant']['id']}",
        if_match=1,
        json={"first_name": "Changed"},
    ),
    ("POST", _C + "/applicants/{applicant_id}/removal"): lambda f, s: f.request(
        "POST",
        f"{_case(s)}/applicants/{s['second']['id']}/removal",
        if_match=1,
        json={"removal_reason": "replaced"},
    ),
    ("POST", _C + "/applicants/{applicant_id}/reinstatement"): lambda f, s: f.request(
        "POST",
        f"{_case(s)}/applicants/{s['removed']['id']}/reinstatement",
        if_match=s["removed"]["version"],
        json={"note": "Back"},
    ),
    ("POST", _F + "/income-sources"): lambda f, s: f.request(
        "POST",
        f"{s['base']}/income-sources",
        json={"applicant_id": s["second"]["id"], "kind": "pension"},
    ),
    ("PATCH", _F + "/income-sources/{income_source_id}"): lambda f, s: f.request(
        "PATCH",
        f"{s['base']}/income-sources/{s['source']['id']}",
        if_match=1,
        json={"name": "Acme Ltd"},
    ),
    ("POST", _F + "/commitments"): lambda f, s: f.request(
        "POST",
        f"{s['base']}/commitments",
        json={"kind": "car_finance", "parties": [{"applicant_id": s["second"]["id"]}]},
    ),
    ("PATCH", _F + "/commitments/{commitment_id}"): lambda f, s: f.request(
        "PATCH",
        f"{s['base']}/commitments/{s['commitment']['id']}",
        if_match=1,
        json={"status": "settled"},
    ),
    ("POST", _F + "/expenditure"): lambda f, s: f.request(
        "POST", f"{s['base']}/expenditure", json={"category": "transport", "scope": "household"}
    ),
    ("PATCH", _F + "/expenditure/{expenditure_item_id}"): lambda f, s: f.request(
        "PATCH",
        f"{s['base']}/expenditure/{s['spend']['id']}",
        if_match=1,
        json={"category": "insurance"},
    ),
    ("POST", _F + "/income-sources/{income_source_id}/withdrawal"): lambda f, s: f.request(
        "POST",
        f"{s['base']}/income-sources/{s['source']['id']}/withdrawal",
        if_match=1,
        json={"reason": "entered_in_error"},
    ),
    ("POST", _F + "/commitments/{commitment_id}/withdrawal"): lambda f, s: f.request(
        "POST",
        f"{s['base']}/commitments/{s['commitment']['id']}/withdrawal",
        if_match=1,
        json={"reason": "duplicate"},
    ),
    ("POST", _F + "/expenditure/{expenditure_item_id}/withdrawal"): lambda f, s: f.request(
        "POST",
        f"{s['base']}/expenditure/{s['spend']['id']}/withdrawal",
        if_match=1,
        json={"reason": "entered_in_error"},
    ),
    ("POST", _F + "/observations"): lambda f, s: f.request(
        "POST",
        f"{s['base']}/observations",
        json={
            "commitment_id": s["commitment"]["id"],
            "measure": "monthly_payment",
            "value": {"type": "money", "amount_minor": 12_000},
            "frequency": "monthly",
        },
    ),
    ("POST", _F + "/observations/{observation_id}/retirement"): lambda f, s: f.request(
        "POST",
        f"{s['base']}/observations/{s['observation']['id']}/retirement",
        json={"reason": "entered_in_error"},
    ),
    ("POST", "/api/v1/cases/{case_id}/documents"): lambda f, s: f.upload(s["case"]["id"]),
    ("POST", "/api/v1/cases/{case_id}/documents/{document_id}/review"): lambda f, s: f.request(
        "POST",
        f"{_case(s)}/documents/{s['document']['id']}/review",
        if_match=s["document"]["version"],
        json={
            "income_source_id": s["source"]["id"],
            "decisions": [{"ordinal": o, "decision": "accept"} for o in s["ordinals"]],
        },
    ),
    ("POST", _C + "/checks/{finding_key}/acknowledgement"): lambda f, s: f.request(
        "POST",
        f"{_case(s)}/checks/{s['finding_key']}/acknowledgement",
        json={"note": "Seen; client to explain"},
    ),
    ("POST", _C + "/bank-accounts/{account_id}/holders"): lambda f, s: f.request(
        "POST",
        f"{_case(s)}/bank-accounts/{s['account_id']}/holders",
        json={"applicant_id": s["second"]["id"]},
    ),
    ("POST", _C + "/profile/approvals"): lambda f, s: f.request(
        "POST",
        f"{_case(s)}/profile/approvals",
        json={
            "fingerprint": s["fingerprint"],
            "attestation_version": "attestation/1",
            "confirmed": True,
            "note": "Reviewed with the client",
        },
    ),
}


async def _extracted_payslip(firm: Firm, state: dict[str, Any]) -> None:
    """Prepare a reviewable document. Upload and processing write their own events, so this
    runs before the event count is taken."""
    from meip.documents.processing import run_once
    from tests.synthetic_docs import PAYSLIPS

    upload = await firm.upload(state["case"]["id"], PAYSLIPS["classic"]()[0].pdf())
    assert upload.status_code == 201, upload.text
    app = firm.client._transport.app  # type: ignore[attr-defined] # the test ASGI app
    worker = (app.state.sessionmaker, app.state.blob_store, app.state.settings)
    while await run_once(*worker):
        pass
    base = f"{_case(state)}/documents/{upload.json()['id']}"
    state["document"] = (await firm.request("GET", base)).json()
    extraction = (await firm.request("GET", f"{base}/extraction")).json()
    state["ordinals"] = [
        f["ordinal"]
        for section in ("summary", "payments", "deductions", "year_to_date")
        for f in extraction[section]
    ]


async def _discrepancy(firm: Firm, state: dict[str, Any]) -> None:
    """A reviewed payslip whose basic pay differs from the declared £3,000 a month (the setup's
    declared observation): a current DECLARED-BASIC discrepancy to acknowledge."""
    await _extracted_payslip(firm, state)
    review = await ACTIONS[("POST", "/api/v1/cases/{case_id}/documents/{document_id}/review")](
        firm, state
    )
    assert review.status_code == 201, review.text
    checks = (await firm.request("GET", f"{_case(state)}/checks")).json()
    state["finding_key"] = next(
        f["key"] for f in checks["findings"] if f["status"] == "discrepancy"
    )


async def _confirmed_account(firm: Firm, state: dict[str, Any]) -> None:
    """A synthetic statement confirmed to a new account held by the first applicant."""
    from meip.documents.processing import run_once
    from tests.synthetic_docs import bank_statement_series, payslip_series, salaries_from

    statement = bank_statement_series(salaries_from(payslip_series(months=1)), months=1)[0]
    upload = await firm.upload(
        state["case"]["id"], statement.document.pdf(), document_type="bank_statement"
    )
    assert upload.status_code == 201, upload.text
    app = firm.client._transport.app  # type: ignore[attr-defined] # the test ASGI app
    while await run_once(app.state.sessionmaker, app.state.blob_store, app.state.settings):
        pass
    base = f"{_case(state)}/documents/{upload.json()['id']}"
    document = (await firm.request("GET", base)).json()
    extraction = (await firm.request("GET", f"{base}/extraction")).json()
    review = await firm.request(
        "POST",
        f"{base}/review",
        if_match=document["version"],
        json={
            "decisions": [
                {"ordinal": f["ordinal"], "decision": "accept"} for f in extraction["summary"]
            ],
            "new_account": {"holder_ids": [state["applicant"]["id"]]},
        },
    )
    assert review.status_code == 201, review.text
    state["account_id"] = review.json()["bank_account"]["id"]


async def _approvable(firm: Firm, state: dict[str, Any]) -> None:
    """Move the case to review (each transition writes its own event) and load the profile's
    fingerprint, so the approval itself is the only event counted."""
    for to in ("collecting_evidence", "in_review"):
        version = (await firm.request("GET", _case(state))).json()["version"]
        resp = await firm.request(
            "POST", f"{_case(state)}/transitions", if_match=version, json={"to_status": to}
        )
        assert resp.status_code == 200, resp.text
    profile = (await firm.request("GET", f"{_case(state)}/profile")).json()
    assert profile["readiness"]["ready"], profile["readiness"]
    state["fingerprint"] = profile["fingerprint"]


# Routes whose action needs state that itself writes audit events.
PREPARE: dict[Route, Callable[[Firm, dict[str, Any]], Awaitable[None]]] = {
    ("POST", "/api/v1/cases/{case_id}/documents/{document_id}/review"): _extracted_payslip,
    ("POST", _C + "/checks/{finding_key}/acknowledgement"): _discrepancy,
    ("POST", _C + "/bank-accounts/{account_id}/holders"): _confirmed_account,
    ("POST", _C + "/profile/approvals"): _approvable,
}


def test_every_audited_route_has_an_action() -> None:
    assert set(ACTIONS) == set(AUDITED_ROUTES)


@pytest.mark.parametrize("route", sorted(AUDITED_ROUTES), ids=lambda r: f"{r[0]} {r[1]}")
async def test_mutation_writes_exactly_one_event(
    firm: Firm, audit_count: CountAudit, route: Route
) -> None:
    state = await _setup(firm)
    if route in PREPARE:
        await PREPARE[route](firm, state)
    before = await audit_count(firm.tenant_id)
    resp = await ACTIONS[route](firm, state)
    assert resp.status_code in (200, 201, 204), resp.text
    assert await audit_count(firm.tenant_id) == before + 1
    latest = (await _tenant_events(firm))[-1]
    assert latest["action"] == AUDITED_ROUTES[route]


async def test_failed_mutation_writes_no_event(firm: Firm, audit_count: CountAudit) -> None:
    case = await firm.create_case()
    before = await audit_count(firm.tenant_id)
    resp = await firm.request(
        "PATCH", f"/api/v1/cases/{case['id']}", if_match=99, json={"term_months": 200}
    )
    assert resp.status_code == 412
    assert await audit_count(firm.tenant_id) == before


async def test_case_history_is_ordered_attributed_and_redacted(firm: Firm) -> None:
    case = await firm.create_case(loan_amount={"amount_minor": 100, "currency": "GBP"})
    applicant = await firm.add_applicant(case["id"], first_name="Distinctive", last_name="Surname")
    await firm.request(
        "PATCH",
        f"/api/v1/cases/{case['id']}/applicants/{applicant['id']}",
        if_match=1,
        json={"first_name": "Different"},
        headers={"X-Request-ID": "trace-123"},
    )
    resp = await firm.request("GET", f"/api/v1/cases/{case['id']}/events")
    assert resp.status_code == 200
    events = resp.json()["items"]
    assert [e["action"] for e in events] == ["case.created", "applicant.added", "applicant.updated"]
    assert all(e["actor_type"] == "user" and e["actor_user_id"] for e in events)
    # Amounts are erasable: marked in the permanent record, values held in details.
    assert events[0]["changes"]["loan_amount_minor"] == {"changed": True}
    assert events[0]["details"]["loan_amount_minor"] == {"from": None, "to": 100}
    assert events[0]["changes"]["purpose"] == {"from": None, "to": "purchase"}
    assert events[2]["changes"] == {"first_name": {"changed": True}}
    assert events[2]["details"] is None  # identifiers are not stored even as erasable values
    assert events[2]["request_id"] == "trace-123"
    assert "Distinctive" not in resp.text
    assert "Surname" not in resp.text


async def test_close_reason_recorded(firm: Firm) -> None:
    case = await firm.create_case()
    await firm.request(
        "POST",
        f"/api/v1/cases/{case['id']}/transitions",
        if_match=1,
        json={"to_status": "closed", "closure_reason": "not_proceeding", "note": "Client withdrew"},
    )
    events = (await firm.request("GET", f"/api/v1/cases/{case['id']}/events")).json()["items"]
    assert events[-1]["changes"] == {
        "status": {"from": "draft", "to": "closed"},
        "closure_reason": {"from": None, "to": "not_proceeding"},
        "note": {"changed": True},
    }
    assert events[-1]["details"] == {"note": "Client withdrew"}


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
        # CASCADE would also take audit_event_details; the immutability trigger must still win.
        "TRUNCATE audit_events CASCADE",
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
