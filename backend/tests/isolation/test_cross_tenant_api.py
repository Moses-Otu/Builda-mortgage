"""Acceptance criterion 4: a user of firm B gets 404 for every firm-A resource.

The route table is enumerated so that any new route with a resource identifier must be
added here, or the coverage test fails.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from fastapi import FastAPI

from tests.conftest import Firm, MakeFirm

# (method, route template, request kwargs). Bodies are valid so that only tenancy decides.
PROBES: list[tuple[str, str, dict[str, Any]]] = [
    ("GET", "/api/v1/cases/{case_id}", {}),
    ("PATCH", "/api/v1/cases/{case_id}", {"json": {"version": 1, "term_months": 240}}),
    (
        "POST",
        "/api/v1/cases/{case_id}/transitions",
        {"json": {"to_status": "closed", "version": 1, "reason": "x"}},
    ),
    ("GET", "/api/v1/cases/{case_id}/events", {}),
    ("GET", "/api/v1/cases/{case_id}/applicants", {}),
    ("POST", "/api/v1/cases/{case_id}/applicants", {"json": {"first_name": "A", "last_name": "B"}}),
    ("GET", "/api/v1/cases/{case_id}/applicants/{applicant_id}", {}),
    (
        "PATCH",
        "/api/v1/cases/{case_id}/applicants/{applicant_id}",
        {"json": {"version": 1, "first_name": "X"}},
    ),
    ("DELETE", "/api/v1/cases/{case_id}/applicants/{applicant_id}", {"params": {"version": 1}}),
    ("PATCH", "/api/v1/members/{membership_id}", {"json": {"version": 1, "role": "read_only"}}),
    # Not tenant-scoped, but an attacker must not be able to accept someone else's invitation.
    ("POST", "/api/v1/me/invitations/{membership_id}/accept", {}),
]


def test_probes_cover_every_parameterised_route(app: FastAPI) -> None:
    routes = {
        (method.upper(), path)
        for path, operations in app.openapi()["paths"].items()
        if "{" in path
        for method in operations
    }
    assert routes == {(m, p) for m, p, _ in PROBES}


@pytest.fixture
async def victim(make_firm: MakeFirm) -> tuple[Firm, dict[str, str]]:
    firm = await make_firm("Victim Firm")
    case = await firm.create_case()
    applicant = await firm.add_applicant(case["id"])
    # A *pending* invitation, so the accept probe targets something actually acceptable.
    member = await firm.add_member("adviser", accept=False)
    ids = {
        "case_id": case["id"],
        "applicant_id": applicant["id"],
        "membership_id": str(firm.members[member]),
    }
    return firm, ids


@pytest.mark.parametrize(("method", "template", "kwargs"), PROBES, ids=lambda p: str(p))
async def test_foreign_firm_gets_404(
    victim: tuple[Firm, dict[str, str]],
    make_firm: MakeFirm,
    method: str,
    template: str,
    kwargs: dict[str, Any],
) -> None:
    owner, ids = victim
    attacker = await make_firm("Attacker Firm")
    path = template.format(**ids)

    resp = await attacker.request(method, path, **kwargs)
    assert resp.status_code == 404, resp.text

    # And the victim's data is untouched.
    case = await owner.request("GET", f"/api/v1/cases/{ids['case_id']}")
    assert case.json()["version"] == 1
    assert case.json()["status"] == "draft"


async def test_list_endpoints_only_show_own_firm(
    victim: tuple[Firm, dict[str, str]], make_firm: MakeFirm
) -> None:
    other = await make_firm()
    await other.create_case()
    cases = (await other.request("GET", "/api/v1/cases")).json()
    assert cases["total"] == 1
    members = (await other.request("GET", "/api/v1/members")).json()
    assert len(members) == 1


async def test_concurrent_requests_for_different_firms_stay_isolated(
    make_firm: MakeFirm,
) -> None:
    """Guards against tenant settings leaking between pooled connections."""
    firms = [await make_firm(f"Firm {i}") for i in range(4)]
    expected: dict[uuid.UUID, set[str]] = {}
    for f in firms:
        expected[f.tenant_id] = {(await f.create_case())["id"] for _ in range(3)}

    async def list_ids(f: Firm) -> tuple[uuid.UUID, set[str]]:
        page = (await f.request("GET", "/api/v1/cases")).json()
        return f.tenant_id, {c["id"] for c in page["items"]}

    results = await asyncio.gather(*(list_ids(f) for f in firms for _ in range(10)))
    for tenant_id, ids in results:
        assert ids == expected[tenant_id]
