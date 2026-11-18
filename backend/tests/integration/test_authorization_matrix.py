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

from tests.conftest import Firm

ADMIN, ADVISER, CASE_ADMIN, READ_ONLY = "firm_admin", "adviser", "case_administrator", "read_only"
ALL_ROLES = (ADMIN, ADVISER, CASE_ADMIN, READ_ONLY)
CASE_WORKERS = frozenset({ADMIN, ADVISER, CASE_ADMIN})
EVERYONE = frozenset(ALL_ROLES)

State = dict[str, Any]


@dataclass(frozen=True)
class Probe:
    name: str
    method: str
    template: str
    allowed: frozenset[str]
    request: Callable[[State], dict[str, Any]] = field(default=lambda s: {})


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
        lambda s: {"json": {"version": s["member_version"], "role": "adviser"}},
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
        lambda s: {"json": {"version": 1, "term_months": 300}},
    ),
    Probe(
        "transition case",
        "POST",
        "/api/v1/cases/{case_id}/transitions",
        CASE_WORKERS,
        lambda s: {"json": {"to_status": "collecting_evidence", "version": 1}},
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
        lambda s: {"json": {"version": 1, "first_name": "Changed"}},
    ),
    Probe(
        "remove applicant",
        "DELETE",
        "/api/v1/cases/{case_id}/applicants/{applicant_id}",
        CASE_WORKERS,
        lambda s: {"params": {"version": 1}},
    ),
    # Conditional permissions, enforced inside services on the same routes.
    Probe(
        "close case",
        "POST",
        "/api/v1/cases/{case_id}/transitions",
        frozenset({ADMIN, ADVISER}),
        lambda s: {"json": {"to_status": "closed", "version": 1, "reason": "Withdrawn"}},
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
        lambda s: {"json": {"version": 1, "adviser_membership_id": s["adviser_membership_id"]}},
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
    target = await firm.add_member("read_only")
    adviser = await firm.add_member("adviser")
    return {
        "case_id": case["id"],
        "applicant_id": applicant["id"],
        "membership_id": str(firm.members[target]),
        "member_version": 2,  # invited (1) -> accepted (2)
        "adviser_membership_id": str(firm.members[adviser]),
    }


@pytest.mark.parametrize("role", ALL_ROLES)
@pytest.mark.parametrize("probe", PROBES, ids=lambda p: p.name)
async def test_role_matrix(firm: Firm, probe: Probe, role: str) -> None:
    actor = await firm.add_member(role)
    state = await _state(firm)
    resp = await firm.request(
        probe.method, probe.template.format(**state), as_=actor, **probe.request(state)
    )
    if role in probe.allowed:
        assert resp.status_code != 403, f"{role} should be allowed to {probe.name}: {resp.text}"
        assert resp.status_code < 500, resp.text
    else:
        assert resp.status_code == 403, f"{role} must not be allowed to {probe.name}"
