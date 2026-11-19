"""Role-by-endpoint authorization matrix.

The expected matrix is written out explicitly rather than derived from ROLE_PERMISSIONS, so
any change to who may do what (including an accidental weakening) fails here and must be
made deliberately. Every tenant-scoped route must appear, which the coverage test enforces.

"Allowed" means the request is not rejected with 403; it may still fail for other reasons
(e.g. 409, 422), which is outside the scope of this test.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import pytest
from fastapi import FastAPI

from tests.conftest import Firm, synthetic_pdf

ADMIN, ADVISER, CASE_ADMIN, READ_ONLY = "firm_admin", "adviser", "case_administrator", "read_only"
ALL_ROLES = (ADMIN, ADVISER, CASE_ADMIN, READ_ONLY)
CASE_WORKERS = frozenset({ADMIN, ADVISER, CASE_ADMIN})
EVERYONE = frozenset(ALL_ROLES)

State = dict[str, Any]
_F = "/api/v1/cases/{case_id}/financial"


@dataclass(frozen=True)
class Probe:
    name: str
    method: str
    template: str
    allowed: frozenset[str]
    request: Callable[[State], dict[str, Any]] = field(default=lambda s: {})
    case_key: str = "case_id"
    applicant_key: str = "applicant_id"
    """Which prepared case the probe targets (e.g. a closed one for reopening)."""


PROBES: list[Probe] = [
    Probe("list members", "GET", "/api/v1/members", EVERYONE),
    Probe(
        "invite member",
        "POST",
        "/api/v1/members",
        frozenset({ADMIN}),
        lambda s: {"json": {"idp_subject": "matrix-invitee", "role": "read_only"}},
    ),
    Probe(
        "update member",
        "PATCH",
        "/api/v1/members/{membership_id}",
        frozenset({ADMIN}),
        lambda s: {"if_match": s["member_version"], "json": {"role": "adviser"}},
    ),
    Probe("list cases", "GET", "/api/v1/cases", EVERYONE),
    Probe(
        "create case",
        "POST",
        "/api/v1/cases",
        CASE_WORKERS,
        lambda s: {"json": {"purpose": "purchase"}},
    ),
    Probe("get case", "GET", "/api/v1/cases/{case_id}", EVERYONE),
    Probe(
        "update case",
        "PATCH",
        "/api/v1/cases/{case_id}",
        CASE_WORKERS,
        lambda s: {"if_match": 1, "json": {"term_months": 300}},
    ),
    Probe(
        "transition case",
        "POST",
        "/api/v1/cases/{case_id}/transitions",
        CASE_WORKERS,
        lambda s: {"if_match": 1, "json": {"to_status": "collecting_evidence"}},
    ),
    Probe("case events", "GET", "/api/v1/cases/{case_id}/events", EVERYONE),
    Probe("list applicants", "GET", "/api/v1/cases/{case_id}/applicants", EVERYONE),
    Probe(
        "add applicant",
        "POST",
        "/api/v1/cases/{case_id}/applicants",
        CASE_WORKERS,
        lambda s: {"json": {"first_name": "M", "last_name": "N"}},
    ),
    Probe("get applicant", "GET", "/api/v1/cases/{case_id}/applicants/{applicant_id}", EVERYONE),
    Probe(
        "update applicant",
        "PATCH",
        "/api/v1/cases/{case_id}/applicants/{applicant_id}",
        CASE_WORKERS,
        lambda s: {"if_match": 1, "json": {"first_name": "Changed"}},
    ),
    Probe(
        "remove applicant",
        "POST",
        "/api/v1/cases/{case_id}/applicants/{applicant_id}/removal",
        CASE_WORKERS,
        lambda s: {"if_match": 1, "json": {"removal_reason": "replaced"}},
    ),
    Probe(
        "reinstate applicant",
        "POST",
        "/api/v1/cases/{case_id}/applicants/{applicant_id}/reinstatement",
        CASE_WORKERS,
        lambda s: {"if_match": s["removed_version"], "json": {"note": "Back"}},
        applicant_key="removed_applicant_id",
    ),
    # Conditional permissions, enforced inside services on the same routes.
    Probe(
        "close case",
        "POST",
        "/api/v1/cases/{case_id}/transitions",
        frozenset({ADMIN, ADVISER}),
        lambda s: {
            "if_match": 1,
            "json": {
                "to_status": "closed",
                "closure_reason": "not_proceeding",
                "note": "Withdrawn",
            },
        },
    ),
    Probe(
        "reopen case",
        "POST",
        "/api/v1/cases/{case_id}/transitions",
        frozenset({ADMIN, ADVISER}),
        lambda s: {
            "if_match": 2,
            "json": {"to_status": "collecting_evidence", "note": "Client returned"},
        },
        case_key="closed_case_id",
    ),
    Probe(
        "create case with adviser",
        "POST",
        "/api/v1/cases",
        frozenset({ADMIN, ADVISER}),
        lambda s: {
            "json": {"purpose": "purchase", "adviser_membership_id": s["adviser_membership_id"]}
        },
    ),
    Probe(
        "reassign adviser",
        "PATCH",
        "/api/v1/cases/{case_id}",
        frozenset({ADMIN, ADVISER}),
        lambda s: {"if_match": 1, "json": {"adviser_membership_id": s["adviser_membership_id"]}},
    ),
    Probe("get firm settings", "GET", "/api/v1/firm/settings", EVERYONE),
    Probe(
        "change firm settings",
        "PATCH",
        "/api/v1/firm/settings",
        frozenset({ADMIN}),
        lambda s: {"if_match": 1, "json": {"max_applicants": 5}},
    ),
    # --- financial ---------------------------------------------------------------------
    Probe("list measures", "GET", "/api/v1/financial/measures", EVERYONE),
    Probe("list income sources", "GET", _F + "/income-sources", EVERYONE),
    Probe(
        "add income source",
        "POST",
        _F + "/income-sources",
        CASE_WORKERS,
        lambda s: {"json": {"applicant_id": s["applicant_id"], "kind": "pension"}},
    ),
    Probe(
        "update income source",
        "PATCH",
        _F + "/income-sources/{income_source_id}",
        CASE_WORKERS,
        lambda s: {"if_match": 1, "json": {"name": "Acme"}},
    ),
    Probe("list commitments", "GET", _F + "/commitments", EVERYONE),
    Probe(
        "add commitment",
        "POST",
        _F + "/commitments",
        CASE_WORKERS,
        lambda s: {
            "json": {"kind": "personal_loan", "parties": [{"applicant_id": s["applicant_id"]}]}
        },
    ),
    Probe(
        "update commitment",
        "PATCH",
        _F + "/commitments/{commitment_id}",
        CASE_WORKERS,
        lambda s: {"if_match": 1, "json": {"status": "settled"}},
    ),
    Probe("list expenditure", "GET", _F + "/expenditure", EVERYONE),
    Probe(
        "add expenditure",
        "POST",
        _F + "/expenditure",
        CASE_WORKERS,
        lambda s: {"json": {"category": "utilities", "scope": "household"}},
    ),
    Probe(
        "update expenditure",
        "PATCH",
        _F + "/expenditure/{expenditure_item_id}",
        CASE_WORKERS,
        lambda s: {"if_match": 1, "json": {"category": "transport"}},
    ),
    Probe(
        "withdraw income source",
        "POST",
        _F + "/income-sources/{income_source_id}/withdrawal",
        CASE_WORKERS,
        lambda s: {"if_match": 1, "json": {"reason": "entered_in_error"}},
    ),
    Probe(
        "withdraw commitment",
        "POST",
        _F + "/commitments/{commitment_id}/withdrawal",
        CASE_WORKERS,
        lambda s: {"if_match": 1, "json": {"reason": "duplicate"}},
    ),
    Probe(
        "withdraw expenditure",
        "POST",
        _F + "/expenditure/{expenditure_item_id}/withdrawal",
        CASE_WORKERS,
        lambda s: {"if_match": 1, "json": {"reason": "entered_in_error"}},
    ),
    Probe("list observations", "GET", _F + "/observations", EVERYONE),
    Probe(
        "record observation",
        "POST",
        _F + "/observations",
        CASE_WORKERS,
        lambda s: {
            "json": {
                "commitment_id": s["commitment_id"],
                "measure": "outstanding_balance",
                "value": {"type": "money", "amount_minor": 50_000},
            }
        },
    ),
    Probe(
        "retire observation",
        "POST",
        _F + "/observations/{observation_id}/retirement",
        CASE_WORKERS,
        lambda s: {"json": {"reason": "entered_in_error"}},
    ),
    Probe("list documents", "GET", "/api/v1/cases/{case_id}/documents", EVERYONE),
    Probe(
        "upload document",
        "POST",
        "/api/v1/cases/{case_id}/documents",
        CASE_WORKERS,
        lambda s: {
            "params": {"document_type": "payslip"},
            "content": synthetic_pdf(),
            "headers": {"Content-Type": "application/pdf"},
        },
    ),
    Probe("get document", "GET", "/api/v1/cases/{case_id}/documents/{document_id}", EVERYONE),
    Probe(
        "download original",
        "GET",
        "/api/v1/cases/{case_id}/documents/{document_id}/content",
        EVERYONE,
    ),
]

# Routes that do not act within a firm and so have no role-based permission.
NOT_TENANT_SCOPED = {
    ("GET", "/api/v1/me"),
    ("POST", "/api/v1/me/invitations/{membership_id}/accept"),
}


def test_matrix_covers_every_api_route(app: FastAPI) -> None:
    routes = {
        (method.upper(), path)
        for path, operations in app.openapi()["paths"].items()
        if path.startswith("/api/")
        for method in operations
    }
    assert routes - NOT_TENANT_SCOPED == {(p.method, p.template) for p in PROBES}


async def _state(firm: Firm) -> State:
    case = await firm.create_case()
    applicant = await firm.add_applicant(case["id"])
    gone = await firm.add_applicant(case["id"])
    removal = await firm.request(
        "POST",
        f"/api/v1/cases/{case['id']}/applicants/{gone['id']}/removal",
        if_match=1,
        json={"removal_reason": "added_in_error"},
    )
    assert removal.status_code == 200, removal.text
    closed = await firm.create_case()
    await firm.add_applicant(closed["id"])
    closing = await firm.request(
        "POST",
        f"/api/v1/cases/{closed['id']}/transitions",
        if_match=1,
        json={"to_status": "closed", "closure_reason": "not_proceeding"},
    )
    assert closing.status_code == 200, closing.text
    target = await firm.add_member("read_only")
    adviser = await firm.add_member("adviser")
    base = f"/api/v1/cases/{case['id']}/financial"
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
            "commitment_id": commitment.json()["id"],
            "measure": "monthly_payment",
            "value": {"type": "money", "amount_minor": 2_500},
            "frequency": "monthly",
        },
    )
    assert observation.status_code == 201, observation.text
    document = await firm.upload(case["id"])
    assert document.status_code == 201, document.text
    return {
        "document_id": document.json()["id"],
        "income_source_id": source.json()["id"],
        "commitment_id": commitment.json()["id"],
        "expenditure_item_id": spend.json()["id"],
        "observation_id": observation.json()["id"],
        "case_id": case["id"],
        "closed_case_id": closed["id"],
        "applicant_id": applicant["id"],
        "removed_applicant_id": gone["id"],
        "removed_version": removal.json()["version"],
        "membership_id": str(firm.members[target]),
        "member_version": 2,  # invited (1) -> accepted (2)
        "adviser_membership_id": str(firm.members[adviser]),
    }


@pytest.mark.parametrize("role", ALL_ROLES)
@pytest.mark.parametrize("probe", PROBES, ids=lambda p: p.name)
async def test_role_matrix(firm: Firm, probe: Probe, role: str) -> None:
    actor = await firm.add_member(role)
    state = await _state(firm)
    path = probe.template.format(
        **{
            **state,
            "case_id": state[probe.case_key],
            "applicant_id": state[probe.applicant_key],
        }
    )
    resp = await firm.request(probe.method, path, as_=actor, **probe.request(state))
    if role in probe.allowed:
        assert resp.status_code != 403, f"{role} should be allowed to {probe.name}: {resp.text}"
        assert resp.status_code < 500, resp.text
    else:
        assert resp.status_code == 403, f"{role} must not be allowed to {probe.name}"
