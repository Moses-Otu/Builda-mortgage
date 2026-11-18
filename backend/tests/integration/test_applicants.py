from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest

from tests.conftest import Firm


def _path(case_id: str, applicant_id: str | None = None, action: str | None = None) -> str:
    base = f"/api/v1/cases/{case_id}/applicants"
    if applicant_id:
        base = f"{base}/{applicant_id}"
    return f"{base}/{action}" if action else base


async def _remove(firm: Firm, case_id: str, applicant: dict[str, Any], **body: Any) -> Any:
    return await firm.request(
        "POST",
        _path(case_id, applicant["id"], "removal"),
        if_match=applicant["version"],
        json={"removal_reason": "no_longer_applying", **body},
    )


async def _reinstate(firm: Firm, case_id: str, applicant_id: str, version: int) -> Any:
    return await firm.request(
        "POST",
        _path(case_id, applicant_id, "reinstatement"),
        if_match=version,
        json={"note": "Applying again"},
    )


class TestRolesAndPrimary:
    async def test_first_borrower_becomes_primary(self, firm: Firm) -> None:
        case = await firm.create_case()
        guarantor = await firm.add_applicant(case["id"], party_role="guarantor", on_title=False)
        first = await firm.add_applicant(case["id"])
        second = await firm.add_applicant(case["id"], on_title=True)
        assert (guarantor["is_primary"], first["is_primary"], second["is_primary"]) == (
            False,
            True,
            False,
        )
        assert guarantor["party_role"] == "guarantor"
        assert (first["on_title"], second["on_title"]) == (None, True)

    async def test_moving_the_primary_flag(self, firm: Firm) -> None:
        case = await firm.create_case()
        first = await firm.add_applicant(case["id"])
        second = await firm.add_applicant(case["id"])
        resp = await firm.request(
            "PATCH", _path(case["id"], second["id"]), if_match=1, json={"is_primary": True}
        )
        assert resp.status_code == 200, resp.text
        listing = {a["id"]: a for a in (await firm.request("GET", _path(case["id"]))).json()}
        assert listing[second["id"]]["is_primary"] is True
        assert listing[first["id"]]["is_primary"] is False

    async def test_primary_flag_cannot_be_cleared_directly(self, firm: Firm) -> None:
        case = await firm.create_case()
        first = await firm.add_applicant(case["id"])
        resp = await firm.request(
            "PATCH", _path(case["id"], first["id"]), if_match=1, json={"is_primary": False}
        )
        assert resp.status_code == 422

    async def test_primary_must_be_a_borrower(self, firm: Firm) -> None:
        case = await firm.create_case()
        first = await firm.add_applicant(case["id"])
        resp = await firm.request(
            "PATCH", _path(case["id"], first["id"]), if_match=1, json={"party_role": "guarantor"}
        )
        assert resp.status_code == 422
        guarantor = await firm.add_applicant(case["id"], party_role="guarantor")
        resp = await firm.request(
            "PATCH", _path(case["id"], guarantor["id"]), if_match=1, json={"is_primary": True}
        )
        assert resp.status_code == 422

    async def test_employment_status_is_no_longer_accepted(self, firm: Firm) -> None:
        case = await firm.create_case()
        resp = await firm.request(
            "POST",
            _path(case["id"]),
            json={"first_name": "A", "last_name": "B", "employment_status": "employed"},
        )
        assert resp.status_code == 422


class TestRemoval:
    async def test_removal_keeps_the_applicant_and_hides_it_by_default(self, firm: Firm) -> None:
        case = await firm.create_case()
        first = await firm.add_applicant(case["id"])
        second = await firm.add_applicant(case["id"])

        removed = await _remove(firm, case["id"], second, note="Moved abroad")
        assert removed.status_code == 200, removed.text
        body = removed.json()
        assert body["id"] == second["id"]
        assert (body["status"], body["removal_reason"]) == ("removed", "no_longer_applying")
        assert body["removed_at"] is not None

        active = (await firm.request("GET", _path(case["id"]))).json()
        assert [a["id"] for a in active] == [first["id"]]
        everyone = (
            await firm.request("GET", _path(case["id"]), params={"include_removed": True})
        ).json()
        assert {a["id"] for a in everyone} == {first["id"], second["id"]}
        assert (await firm.request("GET", _path(case["id"], second["id"]))).status_code == 200

        case_now = (await firm.request("GET", f"/api/v1/cases/{case['id']}")).json()
        assert case_now["applicant_count"] == 1

    async def test_hard_delete_is_gone(self, firm: Firm) -> None:
        case = await firm.create_case()
        applicant = await firm.add_applicant(case["id"])
        resp = await firm.request("DELETE", _path(case["id"], applicant["id"]), if_match=1)
        assert resp.status_code == 405

    async def test_removed_applicant_frees_its_display_order(self, firm: Firm) -> None:
        case = await firm.create_case()
        await firm.add_applicant(case["id"])
        second = await firm.add_applicant(case["id"])
        await _remove(firm, case["id"], second)
        newcomer = await firm.add_applicant(case["id"])
        assert newcomer["display_order"] == 2
        assert newcomer["id"] != second["id"]

    async def test_removing_the_primary_requires_choosing_a_new_one(self, firm: Firm) -> None:
        case = await firm.create_case()
        first = await firm.add_applicant(case["id"])
        second = await firm.add_applicant(case["id"])
        guarantor = await firm.add_applicant(case["id"], party_role="guarantor")

        assert (await _remove(firm, case["id"], first)).status_code == 422
        to_guarantor = await _remove(
            firm, case["id"], first, new_primary_applicant_id=guarantor["id"]
        )
        assert to_guarantor.status_code == 422

        ok = await _remove(firm, case["id"], first, new_primary_applicant_id=second["id"])
        assert ok.status_code == 200, ok.text
        assert ok.json()["is_primary"] is False
        now = (await firm.request("GET", _path(case["id"], second["id"]))).json()
        assert now["is_primary"] is True

    async def test_new_primary_only_when_removing_the_primary(self, firm: Firm) -> None:
        case = await firm.create_case()
        first = await firm.add_applicant(case["id"])
        second = await firm.add_applicant(case["id"])
        resp = await _remove(firm, case["id"], second, new_primary_applicant_id=first["id"])
        assert resp.status_code == 422

    async def test_removing_the_only_borrower_blocks_progress(self, firm: Firm) -> None:
        case = await firm.create_case()
        only = await firm.add_applicant(case["id"])
        assert (await _remove(firm, case["id"], only)).status_code == 200
        move = await firm.request(
            "POST",
            f"/api/v1/cases/{case['id']}/transitions",
            if_match=1,
            json={"to_status": "collecting_evidence"},
        )
        assert move.status_code == 422

    async def test_other_reason_needs_a_note(self, firm: Firm) -> None:
        case = await firm.create_case()
        await firm.add_applicant(case["id"])
        second = await firm.add_applicant(case["id"])
        resp = await _remove(firm, case["id"], second, removal_reason="other")
        assert resp.status_code == 422

    async def test_removed_applicant_cannot_be_edited_or_removed_again(self, firm: Firm) -> None:
        case = await firm.create_case()
        await firm.add_applicant(case["id"])
        second = await firm.add_applicant(case["id"])
        removed = (await _remove(firm, case["id"], second)).json()
        edit = await firm.request(
            "PATCH",
            _path(case["id"], second["id"]),
            if_match=removed["version"],
            json={"first_name": "Z"},
        )
        assert edit.status_code == 409
        again = await _remove(firm, case["id"], removed)
        assert again.status_code == 409

    async def test_removal_is_audited_with_reason_in_erasable_details(self, firm: Firm) -> None:
        case = await firm.create_case()
        await firm.add_applicant(case["id"])
        second = await firm.add_applicant(case["id"])
        await _remove(firm, case["id"], second, removal_reason="other", note="Joint plans changed")
        events = (await firm.request("GET", f"/api/v1/cases/{case['id']}/events")).json()
        removal = events["items"][-1]
        assert removal["action"] == "applicant.removed"
        assert removal["entity_id"] == second["id"]
        assert removal["changes"]["status"] == {"from": "active", "to": "removed"}
        assert removal["changes"]["removal_reason"] == {"from": None, "to": "other"}
        assert removal["details"] == {"note": "Joint plans changed"}


class TestReinstatement:
    async def test_reinstatement_restores_the_same_applicant(self, firm: Firm) -> None:
        case = await firm.create_case()
        await firm.add_applicant(case["id"])
        second = await firm.add_applicant(case["id"])
        removed = (await _remove(firm, case["id"], second)).json()

        back = await _reinstate(firm, case["id"], second["id"], removed["version"])
        assert back.status_code == 200, back.text
        body = back.json()
        assert body["id"] == second["id"]
        assert (body["status"], body["removed_at"], body["removal_reason"]) == (
            "active",
            None,
            None,
        )
        assert body["display_order"] == 2

    async def test_reinstatement_does_not_take_back_the_primary_flag(self, firm: Firm) -> None:
        case = await firm.create_case()
        first = await firm.add_applicant(case["id"])
        second = await firm.add_applicant(case["id"])
        removed = (
            await _remove(firm, case["id"], first, new_primary_applicant_id=second["id"])
        ).json()
        back = (await _reinstate(firm, case["id"], first["id"], removed["version"])).json()
        assert back["is_primary"] is False
        now = (await firm.request("GET", _path(case["id"], second["id"]))).json()
        assert now["is_primary"] is True

    async def test_reinstated_sole_borrower_becomes_primary_again(self, firm: Firm) -> None:
        case = await firm.create_case()
        only = await firm.add_applicant(case["id"])
        removed = (await _remove(firm, case["id"], only)).json()
        back = (await _reinstate(firm, case["id"], only["id"], removed["version"])).json()
        assert back["is_primary"] is True

    async def test_reinstatement_takes_a_free_slot_if_its_own_was_reused(self, firm: Firm) -> None:
        case = await firm.create_case()
        await firm.add_applicant(case["id"])
        second = await firm.add_applicant(case["id"])
        removed = (await _remove(firm, case["id"], second)).json()
        newcomer = await firm.add_applicant(case["id"])
        assert newcomer["display_order"] == 2
        back = (await _reinstate(firm, case["id"], second["id"], removed["version"])).json()
        assert back["display_order"] == 3

    async def test_reinstatement_respects_the_limit(self, firm: Firm) -> None:
        case = await firm.create_case()
        applicants = [await firm.add_applicant(case["id"]) for _ in range(4)]
        removed = (await _remove(firm, case["id"], applicants[3])).json()
        await firm.add_applicant(case["id"])
        back = await _reinstate(firm, case["id"], applicants[3]["id"], removed["version"])
        assert back.status_code == 422

    async def test_only_removed_applicants_can_be_reinstated(self, firm: Firm) -> None:
        case = await firm.create_case()
        applicant = await firm.add_applicant(case["id"])
        assert (await _reinstate(firm, case["id"], applicant["id"], 1)).status_code == 409

    async def test_reinstatement_needs_a_note(self, firm: Firm) -> None:
        case = await firm.create_case()
        await firm.add_applicant(case["id"])
        second = await firm.add_applicant(case["id"])
        removed = (await _remove(firm, case["id"], second)).json()
        resp = await firm.request(
            "POST",
            _path(case["id"], second["id"], "reinstatement"),
            if_match=removed["version"],
            json={},
        )
        assert resp.status_code == 422


class TestEditabilityAndValidation:
    @pytest.mark.parametrize("action", ["removal", "reinstatement"])
    async def test_closed_case_blocks_removal_and_reinstatement(
        self, firm: Firm, action: str
    ) -> None:
        case = await firm.create_case()
        await firm.add_applicant(case["id"])
        second = await firm.add_applicant(case["id"])
        target = second
        if action == "reinstatement":
            target = (await _remove(firm, case["id"], second)).json()
        await firm.request(
            "POST",
            f"/api/v1/cases/{case['id']}/transitions",
            if_match=1,
            json={"to_status": "closed", "closure_reason": "not_proceeding"},
        )
        body = {"removal_reason": "replaced"} if action == "removal" else {"note": "Back again"}
        resp = await firm.request(
            "POST",
            _path(case["id"], second["id"], action),
            if_match=target["version"],
            json=body,
        )
        assert resp.status_code == 409

    async def test_applicants_editable_in_review(self, firm: Firm) -> None:
        case = await firm.create_case()
        applicant = await firm.add_applicant(case["id"])
        for status, version in (("collecting_evidence", 1), ("in_review", 2)):
            await firm.request(
                "POST",
                f"/api/v1/cases/{case['id']}/transitions",
                if_match=version,
                json={"to_status": status},
            )
        resp = await firm.request(
            "PATCH",
            _path(case["id"], applicant["id"]),
            if_match=1,
            json={"last_name": "Corrected"},
        )
        assert resp.status_code == 200

    async def test_max_applicants_enforced(self, firm: Firm) -> None:
        case = await firm.create_case()
        for _ in range(4):
            await firm.add_applicant(case["id"])
        resp = await firm.request(
            "POST", _path(case["id"]), json={"first_name": "E", "last_name": "F"}
        )
        assert resp.status_code == 422

    async def test_update_and_clear_optional_fields(self, firm: Firm) -> None:
        case = await firm.create_case()
        applicant = await firm.add_applicant(case["id"], middle_names="Lee")
        resp = await firm.request(
            "PATCH",
            _path(case["id"], applicant["id"]),
            if_match=1,
            json={"middle_names": None, "on_title": False},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["middle_names"] is None
        assert body["on_title"] is False
        assert body["version"] == 2
        fetched = await firm.request("GET", _path(case["id"], applicant["id"]))
        assert fetched.json() == body

    async def test_required_fields_cannot_be_cleared(self, firm: Firm) -> None:
        case = await firm.create_case()
        applicant = await firm.add_applicant(case["id"])
        for field in ("last_name", "party_role"):
            resp = await firm.request(
                "PATCH", _path(case["id"], applicant["id"]), if_match=1, json={field: None}
            )
            assert resp.status_code == 422

    async def test_noop_update_rejected(self, firm: Firm) -> None:
        case = await firm.create_case()
        applicant = await firm.add_applicant(case["id"], first_name="Sam")
        resp = await firm.request(
            "PATCH", _path(case["id"], applicant["id"]), if_match=1, json={"first_name": "Sam"}
        )
        assert resp.status_code == 422

    async def test_future_date_of_birth_rejected(self, firm: Firm) -> None:
        case = await firm.create_case()
        tomorrow = (date.today() + timedelta(days=1)).isoformat()  # noqa: DTZ011
        resp = await firm.request(
            "POST",
            _path(case["id"]),
            json={"first_name": "A", "last_name": "B", "date_of_birth": tomorrow},
        )
        assert resp.status_code == 422

    async def test_blank_names_rejected(self, firm: Firm) -> None:
        case = await firm.create_case()
        resp = await firm.request(
            "POST", _path(case["id"]), json={"first_name": "  ", "last_name": "B"}
        )
        assert resp.status_code == 422

    async def test_stale_versions_rejected(self, firm: Firm) -> None:
        case = await firm.create_case()
        applicant = await firm.add_applicant(case["id"])
        stale_patch = await firm.request(
            "PATCH", _path(case["id"], applicant["id"]), if_match=9, json={"first_name": "Z"}
        )
        assert stale_patch.status_code == 412
        stale_removal = await _remove(firm, case["id"], {**applicant, "version": 9})
        assert stale_removal.status_code == 412

    async def test_applicant_from_other_case_is_404(self, firm: Firm) -> None:
        case_a = await firm.create_case()
        case_b = await firm.create_case()
        applicant = await firm.add_applicant(case_a["id"])
        resp = await firm.request("GET", _path(case_b["id"], applicant["id"]))
        assert resp.status_code == 404

    async def test_applicants_of_unknown_case_is_404(self, firm: Firm) -> None:
        resp = await firm.request("GET", _path("00000000-0000-7000-8000-000000000000"))
        assert resp.status_code == 404


class TestPrimaryInvariant:
    """Regression test for the pre-commit review (D3): active borrowers always have a primary."""

    async def test_d3_guarantor_who_becomes_the_only_borrower_becomes_primary(
        self, firm: Firm
    ) -> None:
        case = await firm.create_case()
        primary = await firm.add_applicant(case["id"])
        guarantor = await firm.add_applicant(case["id"], party_role="guarantor")
        assert (await _remove(firm, case["id"], primary)).status_code == 200

        blocked = await firm.request(
            "POST",
            f"/api/v1/cases/{case['id']}/transitions",
            if_match=1,
            json={"to_status": "collecting_evidence"},
        )
        assert blocked.status_code == 422  # only a guarantor: no primary borrower

        promoted = await firm.request(
            "PATCH",
            _path(case["id"], guarantor["id"]),
            if_match=guarantor["version"],
            json={"party_role": "borrower"},
        )
        assert promoted.status_code == 200, promoted.text
        assert promoted.json()["is_primary"] is True

        moved = await firm.request(
            "POST",
            f"/api/v1/cases/{case['id']}/transitions",
            if_match=1,
            json={"to_status": "collecting_evidence"},
        )
        assert moved.status_code == 200

    async def test_d3_new_borrower_does_not_steal_an_existing_primary(self, firm: Firm) -> None:
        case = await firm.create_case()
        primary = await firm.add_applicant(case["id"])
        guarantor = await firm.add_applicant(case["id"], party_role="guarantor")
        promoted = await firm.request(
            "PATCH",
            _path(case["id"], guarantor["id"]),
            if_match=1,
            json={"party_role": "borrower"},
        )
        assert promoted.json()["is_primary"] is False
        still = await firm.request("GET", _path(case["id"], primary["id"]))
        assert still.json()["is_primary"] is True
