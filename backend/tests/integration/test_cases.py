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
        if_match=version,
        json={"to_status": to, **extra},
    )


class TestCaseValidation:
    async def test_loan_above_property_value_is_a_warning_not_a_block(self, firm: Firm) -> None:
        resp = await firm.request(
            "POST",
            "/api/v1/cases",
            json={"purpose": "purchase", "property_value": money(100), "loan_amount": money(101)},
        )
        assert resp.status_code == 201
        assert [w["code"] for w in resp.json()["warnings"]] == ["loan_exceeds_property_value"]

    @pytest.mark.parametrize(
        ("term", "warned"), [(59, True), (60, False), (480, False), (481, True)]
    )
    async def test_atypical_term_is_a_warning(self, firm: Firm, term: int, warned: bool) -> None:
        resp = await firm.request(
            "POST", "/api/v1/cases", json={"purpose": "purchase", "term_months": term}
        )
        assert resp.status_code == 201
        codes = [w["code"] for w in resp.json()["warnings"]]
        assert ("term_outside_typical_range" in codes) is warned

    @pytest.mark.parametrize("term", [0, 601])
    async def test_term_outside_sanity_range_rejected(self, firm: Firm, term: int) -> None:
        resp = await firm.request(
            "POST", "/api/v1/cases", json={"purpose": "purchase", "term_months": term}
        )
        assert resp.status_code == 422

    async def test_purpose_and_property_use_are_separate(self, firm: Firm) -> None:
        case = await firm.create_case(purpose="porting", property_use="buy_to_let")
        assert (case["purpose"], case["property_use"]) == ("porting", "buy_to_let")
        updated = await firm.request(
            "PATCH", f"/api/v1/cases/{case['id']}", if_match=1, json={"property_use": None}
        )
        assert updated.json()["property_use"] is None

    async def test_negative_money_rejected(self, firm: Firm) -> None:
        resp = await firm.request(
            "POST", "/api/v1/cases", json={"purpose": "purchase", "loan_amount": money(-1)}
        )
        assert resp.status_code == 422

    async def test_update_recomputes_warnings(self, firm: Firm) -> None:
        case = await firm.create_case(property_value=money(1000))
        assert case["warnings"] == []
        resp = await firm.request(
            "PATCH", f"/api/v1/cases/{case['id']}", if_match=1, json={"loan_amount": money(2000)}
        )
        assert resp.status_code == 200
        assert [w["code"] for w in resp.json()["warnings"]] == ["loan_exceeds_property_value"]

    async def test_purpose_cannot_be_cleared(self, firm: Firm) -> None:
        case = await firm.create_case()
        resp = await firm.request(
            "PATCH", f"/api/v1/cases/{case['id']}", if_match=1, json={"purpose": None}
        )
        assert resp.status_code == 422

    async def test_noop_update_rejected(self, firm: Firm) -> None:
        case = await firm.create_case()
        resp = await firm.request(
            "PATCH", f"/api/v1/cases/{case['id']}", if_match=1, json={"purpose": "purchase"}
        )
        assert resp.status_code == 422

    async def test_stale_version_is_412(self, firm: Firm) -> None:
        case = await firm.create_case()
        ok = await firm.request(
            "PATCH", f"/api/v1/cases/{case['id']}", if_match=1, json={"term_months": 240}
        )
        assert ok.status_code == 200
        stale = await firm.request(
            "PATCH", f"/api/v1/cases/{case['id']}", if_match=1, json={"term_months": 300}
        )
        assert stale.status_code == 412
        assert stale.json()["type"].endswith(":precondition-failed")

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
            "PATCH", f"/api/v1/members/{mid}", if_match=2, json={"status": "deactivated"}
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
            if_match=1,
            json={"adviser_membership_id": None},
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
            if_match=1,
            json={"adviser_membership_id": mid},
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
            if_match=1,
            json={"to_status": "closed", "closure_reason": "not_proceeding"},
        )
        assert resp.status_code == 403


class TestLifecycle:
    async def test_collecting_evidence_requires_an_applicant(self, firm: Firm) -> None:
        case = await firm.create_case()
        resp = await _transition(firm, case["id"], "collecting_evidence", 1)
        assert resp.status_code == 422

    async def test_forward_to_review_and_back(self, firm: Firm) -> None:
        case = await firm.create_case()
        await firm.add_applicant(case["id"])
        assert (await _transition(firm, case["id"], "collecting_evidence", 1)).status_code == 200
        review = await _transition(firm, case["id"], "in_review", 2)
        assert review.json()["status"] == "in_review"
        back = await _transition(firm, case["id"], "collecting_evidence", 3)
        assert back.json()["status"] == "collecting_evidence"
        draft = await _transition(firm, case["id"], "draft", 4)
        assert draft.json()["status"] == "draft"

    @pytest.mark.parametrize("retired", ["processing", "approved"])
    async def test_retired_statuses_are_rejected(self, firm: Firm, retired: str) -> None:
        case = await firm.create_case()
        resp = await _transition(firm, case["id"], retired, 1)
        assert resp.status_code == 422

    async def test_undefined_transition_is_409(self, firm: Firm) -> None:
        case = await firm.create_case()
        resp = await _transition(firm, case["id"], "in_review", 1)
        assert resp.status_code == 409
        assert resp.json()["type"].endswith(":invalid-state-transition")
        assert "available" not in resp.json()

    async def test_verification_is_unavailable_until_approvals_exist(self, firm: Firm) -> None:
        case = await firm.create_case()
        await firm.add_applicant(case["id"])
        await _transition(firm, case["id"], "collecting_evidence", 1)
        await _transition(firm, case["id"], "in_review", 2)
        resp = await _transition(firm, case["id"], "verified", 3)
        assert resp.status_code == 409
        assert resp.json()["available"] is False

    async def test_closing_requires_a_reason_code(self, firm: Firm) -> None:
        case = await firm.create_case()
        assert (await _transition(firm, case["id"], "closed", 1)).status_code == 422
        other = await _transition(firm, case["id"], "closed", 1, closure_reason="other")
        assert other.status_code == 422  # 'other' also needs a note
        closed = await _transition(
            firm, case["id"], "closed", 1, closure_reason="duplicate", note="Same as C-12"
        )
        assert closed.status_code == 200
        body = closed.json()
        assert (body["status"], body["closure_reason"]) == ("closed", "duplicate")
        assert body["closure_note"] == "Same as C-12"

    async def test_closure_reason_only_when_closing(self, firm: Firm) -> None:
        case = await firm.create_case()
        await firm.add_applicant(case["id"])
        resp = await _transition(
            firm, case["id"], "collecting_evidence", 1, closure_reason="duplicate"
        )
        assert resp.status_code == 422

    async def test_closed_case_is_locked_until_reopened(self, firm: Firm) -> None:
        case = await firm.create_case()
        await firm.add_applicant(case["id"])
        await _transition(firm, case["id"], "closed", 1, closure_reason="not_proceeding")

        edit = await firm.request(
            "PATCH", f"/api/v1/cases/{case['id']}", if_match=2, json={"term_months": 240}
        )
        assert edit.status_code == 409
        add = await firm.request(
            "POST",
            f"/api/v1/cases/{case['id']}/applicants",
            json={"first_name": "A", "last_name": "B"},
        )
        assert add.status_code == 409
        assert (await _transition(firm, case["id"], "draft", 2)).status_code == 409

    async def test_reopen_requires_a_note_and_clears_the_closure(self, firm: Firm) -> None:
        case = await firm.create_case()
        await firm.add_applicant(case["id"])
        await _transition(firm, case["id"], "closed", 1, closure_reason="not_proceeding")

        assert (await _transition(firm, case["id"], "collecting_evidence", 2)).status_code == 422
        reopened = await _transition(
            firm, case["id"], "collecting_evidence", 2, note="Client returned"
        )
        assert reopened.status_code == 200
        body = reopened.json()
        assert (body["status"], body["closure_reason"], body["closure_note"]) == (
            "collecting_evidence",
            None,
            None,
        )
        events = (await firm.request("GET", f"/api/v1/cases/{case['id']}/events")).json()
        last = events["items"][-1]
        assert last["changes"]["status"] == {"from": "closed", "to": "collecting_evidence"}
        assert last["changes"]["closure_reason"] == {"from": "not_proceeding", "to": None}
        assert last["details"] == {"note": "Client returned"}

    async def test_case_administrator_cannot_reopen(self, firm: Firm) -> None:
        clerk = await firm.add_member("case_administrator")
        case = await firm.create_case()
        await firm.add_applicant(case["id"])
        await _transition(firm, case["id"], "closed", 1, closure_reason="not_proceeding")
        resp = await firm.request(
            "POST",
            f"/api/v1/cases/{case['id']}/transitions",
            as_=clerk,
            if_match=2,
            json={"to_status": "collecting_evidence", "note": "x"},
        )
        assert resp.status_code == 403


class TestListing:
    async def test_pagination_and_status_filter(self, firm: Firm) -> None:
        ids = [(await firm.create_case())["id"] for _ in range(3)]
        await _transition(firm, ids[0], "closed", 1, closure_reason="duplicate")

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
