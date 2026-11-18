"""Acceptance criterion 3: the core end-to-end case workflow."""

from __future__ import annotations

from tests.conftest import Firm


async def test_create_case_add_two_applicants_update_transition_and_list(firm: Firm) -> None:
    adviser = await firm.add_member("adviser")

    created = await firm.create_case(
        as_=adviser,
        purpose="remortgage",
        external_reference="CRM-1042",
        adviser_membership_id=str(firm.members[adviser]),
        property_value={"amount_minor": 45_000_000, "currency": "GBP"},
        loan_amount={"amount_minor": 30_000_000, "currency": "GBP"},
        term_months=300,
    )
    case_id = created["id"]
    assert created["status"] == "draft"
    assert created["version"] == 1
    assert created["applicant_count"] == 0
    assert created["loan_amount"] == {"amount_minor": 30_000_000, "currency": "GBP"}

    first = await firm.add_applicant(
        case_id,
        first_name="Sam",
        last_name="Taylor",
        date_of_birth="1988-04-12",
        employment_status="employed",
        declared_gross_annual_income={"amount_minor": 5_250_000, "currency": "GBP"},
    )
    second = await firm.add_applicant(case_id, first_name="Jo", last_name="Taylor")
    assert (first["position"], second["position"]) == (1, 2)

    updated = await firm.request(
        "PATCH",
        f"/api/v1/cases/{case_id}",
        as_=adviser,
        json={"version": 1, "term_months": 360, "external_reference": None},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["term_months"] == 360
    assert updated.json()["external_reference"] is None
    assert updated.json()["version"] == 2

    moved = await firm.request(
        "POST",
        f"/api/v1/cases/{case_id}/transitions",
        as_=adviser,
        json={"to_status": "collecting_evidence", "version": 2},
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["status"] == "collecting_evidence"
    assert moved.json()["applicant_count"] == 2

    listed = await firm.request("GET", "/api/v1/cases", as_=adviser)
    assert listed.status_code == 200
    page = listed.json()
    assert page["total"] == 1
    assert page["items"][0]["id"] == case_id
    assert page["items"][0]["applicant_count"] == 2

    applicants = await firm.request("GET", f"/api/v1/cases/{case_id}/applicants")
    assert [a["first_name"] for a in applicants.json()] == ["Sam", "Jo"]
