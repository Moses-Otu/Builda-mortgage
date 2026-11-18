from __future__ import annotations

from typing import Any

import pytest

from tests.conftest import Firm

GBP = "GBP"


def money(minor: int) -> dict[str, Any]:
    return {"amount_minor": minor, "currency": GBP}


async def _transition(firm: Firm, case_id: str, to: str, version: int, **extra: Any) -> Any:
    return await firm.request(
        "POST",
        f"/api/v1/cases/{case_id}/transitions",
        json={"to_status": to, "version": version, **extra},
    )


class TestCaseValidation:
    async def test_loan_cannot_exceed_property_value(self, firm: Firm) -> None:
        resp = await firm.request(
            "POST",
            "/api/v1/cases",
            json={"purpose": "purchase", "property_value": money(100), "loan_amount": money(101)},
        )
        assert resp.status_code == 422
        assert resp.json()["type"].endswith(":business-rule-violation")

    @pytest.mark.parametrize("term", [11, 481])
    async def test_term_bounds(self, firm: Firm, term: int) -> None:
        resp = await firm.request(
            "POST", "/api/v1/cases", json={"purpose": "purchase", "term_months": term}
        )
        assert resp.status_code == 422

    async def test_negative_money_rejected(self, firm: Firm) -> None:
        resp = await firm.request(
            "POST", "/api/v1/cases", json={"purpose": "purchase", "loan_amount": money(-1)}
        )
        assert resp.status_code == 422

    async def test_update_validates_against_existing_values(self, firm: Firm) -> None:
        case = await firm.create_case(property_value=money(1000))
        resp = await firm.request(
            "PATCH", f"/api/v1/cases/{case['id']}", json={"version": 1, "loan_amount": money(2000)}
        )
        assert resp.status_code == 422

    async def test_purpose_cannot_be_cleared(self, firm: Firm) -> None:
        case = await firm.create_case()
        resp = await firm.request(
            "PATCH", f"/api/v1/cases/{case['id']}", json={"version": 1, "purpose": None}
        )
        assert resp.status_code == 422

    async def test_noop_update_rejected(self, firm: Firm) -> None:
        case = await firm.create_case()
        resp = await firm.request(
            "PATCH", f"/api/v1/cases/{case['id']}", json={"version": 1, "purpose": "purchase"}
        )
        assert resp.status_code == 422

    async def test_stale_version_is_409(self, firm: Firm) -> None:
        case = await firm.create_case()
        ok = await firm.request(
            "PATCH", f"/api/v1/cases/{case['id']}", json={"version": 1, "term_months": 240}
        )
        assert ok.status_code == 200
        stale = await firm.request(
            "PATCH", f"/api/v1/cases/{case['id']}", json={"version": 1, "term_months": 300}
        )
        assert stale.status_code == 409
        assert stale.json()["type"].endswith(":version-conflict")

    async def test_unknown_case_is_404(self, firm: Firm) -> None:
        resp = await firm.request("GET", "/api/v1/cases/00000000-0000-7000-8000-000000000000")
        assert resp.status_code == 404


class TestAdviserAssignment:
    async def test_read_only_member_cannot_be_adviser(self, firm: Firm) -> None:
        viewer = await firm.add_member("read_only")
        resp = await firm.request(
            "POST",
            "/api/v1/cases",
            json={"purpose": "purchase", "adviser_membership_id": str(firm.members[viewer])},
        )
        assert resp.status_code == 422

    async def test_deactivated_adviser_cannot_be_assigned(self, firm: Firm) -> None:
        adviser = await firm.add_member("adviser")
        mid = firm.members[adviser]
        deact = await firm.request(
            "PATCH", f"/api/v1/members/{mid}", json={"version": 2, "status": "deactivated"}
        )
        assert deact.status_code == 200
        resp = await firm.request(
            "POST", "/api/v1/cases", json={"purpose": "purchase", "adviser_membership_id": str(mid)}
        )
        assert resp.status_code == 422

    async def test_case_administrator_cannot_assign_or_unassign(self, firm: Firm) -> None:
        clerk = await firm.add_member("case_administrator")
        resp = await firm.request(
            "POST",
            "/api/v1/cases",
            as_=clerk,
            json={"purpose": "purchase", "adviser_membership_id": str(firm.admin_membership_id)},
        )
        assert resp.status_code == 403

        case = await firm.create_case(adviser_membership_id=str(firm.admin_membership_id))
        resp = await firm.request(
            "PATCH",
            f"/api/v1/cases/{case['id']}",
            as_=clerk,
            json={"version": 1, "adviser_membership_id": None},
        )
        assert resp.status_code == 403

    async def test_adviser_can_reassign_and_filter(self, firm: Firm) -> None:
        adviser = await firm.add_member("adviser")
        mid = str(firm.members[adviser])
        case = await firm.create_case()
        await firm.create_case()
        resp = await firm.request(
            "PATCH",
            f"/api/v1/cases/{case['id']}",
            as_=adviser,
            json={"version": 1, "adviser_membership_id": mid},
        )
        assert resp.status_code == 200
        listed = await firm.request("GET", "/api/v1/cases", params={"adviser_membership_id": mid})
        assert [c["id"] for c in listed.json()["items"]] == [case["id"]]


class TestRoles:
    async def test_read_only_can_read_but_not_write(self, firm: Firm) -> None:
        viewer = await firm.add_member("read_only")
        case = await firm.create_case()
        assert (
            await firm.request("GET", f"/api/v1/cases/{case['id']}", as_=viewer)
        ).status_code == 200
        denied = await firm.request(
            "POST", "/api/v1/cases", as_=viewer, json={"purpose": "purchase"}
        )
        assert denied.status_code == 403
        assert denied.json()["type"].endswith(":forbidden")
        denied = await firm.request(
            "POST",
            f"/api/v1/cases/{case['id']}/applicants",
            as_=viewer,
            json={"first_name": "A", "last_name": "B"},
        )
        assert denied.status_code == 403

    async def test_case_administrator_cannot_close(self, firm: Firm) -> None:
        clerk = await firm.add_member("case_administrator")
        case = await firm.create_case(as_=clerk)
        resp = await firm.request(
            "POST",
            f"/api/v1/cases/{case['id']}/transitions",
            as_=clerk,
            json={"to_status": "closed", "version": 1, "reason": "Client withdrew"},
        )
        assert resp.status_code == 403


class TestLifecycle:
    async def test_collecting_evidence_requires_an_applicant(self, firm: Firm) -> None:
        case = await firm.create_case()
        resp = await _transition(firm, case["id"], "collecting_evidence", 1)
        assert resp.status_code == 422

    async def test_back_to_draft(self, firm: Firm) -> None:
        case = await firm.create_case()
        await firm.add_applicant(case["id"])
        assert (await _transition(firm, case["id"], "collecting_evidence", 1)).status_code == 200
        back = await _transition(firm, case["id"], "draft", 2)
        assert back.status_code == 200
        assert back.json()["status"] == "draft"

    async def test_undefined_transition_is_409(self, firm: Firm) -> None:
        case = await firm.create_case()
        resp = await _transition(firm, case["id"], "approved", 1)
        assert resp.status_code == 409
        assert resp.json()["type"].endswith(":invalid-state-transition")
        assert "available" not in resp.json()

    async def test_later_phase_transition_reports_unavailable(self, firm: Firm) -> None:
        case = await firm.create_case()
        await firm.add_applicant(case["id"])
        await _transition(firm, case["id"], "collecting_evidence", 1)
        resp = await _transition(firm, case["id"], "processing", 2)
        assert resp.status_code == 409
        assert resp.json()["available"] is False

    async def test_close_requires_reason_and_is_terminal(self, firm: Firm) -> None:
        case = await firm.create_case()
        assert (await _transition(firm, case["id"], "closed", 1)).status_code == 422
        closed = await _transition(firm, case["id"], "closed", 1, reason="Client withdrew")
        assert closed.status_code == 200
        assert (await _transition(firm, case["id"], "draft", 2)).status_code == 409

        edit = await firm.request(
            "PATCH", f"/api/v1/cases/{case['id']}", json={"version": 2, "term_months": 240}
        )
        assert edit.status_code == 409
        add = await firm.request(
            "POST",
            f"/api/v1/cases/{case['id']}/applicants",
            json={"first_name": "A", "last_name": "B"},
        )
        assert add.status_code == 409


class TestListing:
    async def test_pagination_and_status_filter(self, firm: Firm) -> None:
        ids = [(await firm.create_case())["id"] for _ in range(3)]
        await _transition(firm, ids[0], "closed", 1, reason="Duplicate")

        page = (await firm.request("GET", "/api/v1/cases", params={"limit": 2})).json()
        assert page["total"] == 3
        assert len(page["items"]) == 2
        assert page["items"][0]["id"] == ids[2]  # newest first

        rest = (await firm.request("GET", "/api/v1/cases", params={"limit": 2, "offset": 2})).json()
        assert [c["id"] for c in rest["items"]] == [ids[0]]

        closed = (await firm.request("GET", "/api/v1/cases", params={"status": "closed"})).json()
        assert [c["id"] for c in closed["items"]] == [ids[0]]

    async def test_limit_bounds(self, firm: Firm) -> None:
        resp = await firm.request("GET", "/api/v1/cases", params={"limit": 101})
        assert resp.status_code == 422
