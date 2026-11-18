from __future__ import annotations

from datetime import date, timedelta

from tests.conftest import Firm


def _path(case_id: str, applicant_id: str | None = None) -> str:
    base = f"/api/v1/cases/{case_id}/applicants"
    return f"{base}/{applicant_id}" if applicant_id else base


async def test_max_applicants_enforced(firm: Firm) -> None:
    case = await firm.create_case()
    for _ in range(4):
        await firm.add_applicant(case["id"])
    resp = await firm.request("POST", _path(case["id"]), json={"first_name": "E", "last_name": "F"})
    assert resp.status_code == 422


async def test_removed_position_is_reused(firm: Firm) -> None:
    case = await firm.create_case()
    first = await firm.add_applicant(case["id"])
    await firm.add_applicant(case["id"])

    removed = await firm.request(
        "DELETE", _path(case["id"], first["id"]), params={"version": first["version"]}
    )
    assert removed.status_code == 204
    third = await firm.add_applicant(case["id"])
    assert third["position"] == 1

    gone = await firm.request("GET", _path(case["id"], first["id"]))
    assert gone.status_code == 404


async def test_update_and_clear_optional_fields(firm: Firm) -> None:
    case = await firm.create_case()
    applicant = await firm.add_applicant(
        case["id"],
        middle_names="Lee",
        declared_monthly_commitments={"amount_minor": 45_000, "currency": "GBP"},
    )
    resp = await firm.request(
        "PATCH",
        _path(case["id"], applicant["id"]),
        json={
            "version": 1,
            "middle_names": None,
            "employment_status": "self_employed",
            "declared_monthly_commitments": None,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["middle_names"] is None
    assert body["employment_status"] == "self_employed"
    assert body["declared_monthly_commitments"] is None
    assert body["version"] == 2

    fetched = await firm.request("GET", _path(case["id"], applicant["id"]))
    assert fetched.json() == body


async def test_required_fields_cannot_be_cleared(firm: Firm) -> None:
    case = await firm.create_case()
    applicant = await firm.add_applicant(case["id"])
    resp = await firm.request(
        "PATCH", _path(case["id"], applicant["id"]), json={"version": 1, "last_name": None}
    )
    assert resp.status_code == 422


async def test_noop_update_rejected(firm: Firm) -> None:
    case = await firm.create_case()
    applicant = await firm.add_applicant(case["id"], first_name="Sam")
    resp = await firm.request(
        "PATCH", _path(case["id"], applicant["id"]), json={"version": 1, "first_name": "Sam"}
    )
    assert resp.status_code == 422


async def test_future_date_of_birth_rejected(firm: Firm) -> None:
    case = await firm.create_case()
    tomorrow = (date.today() + timedelta(days=1)).isoformat()  # noqa: DTZ011
    resp = await firm.request(
        "POST",
        _path(case["id"]),
        json={"first_name": "A", "last_name": "B", "date_of_birth": tomorrow},
    )
    assert resp.status_code == 422


async def test_blank_names_rejected(firm: Firm) -> None:
    case = await firm.create_case()
    resp = await firm.request(
        "POST", _path(case["id"]), json={"first_name": "  ", "last_name": "B"}
    )
    assert resp.status_code == 422


async def test_stale_versions_rejected(firm: Firm) -> None:
    case = await firm.create_case()
    applicant = await firm.add_applicant(case["id"])
    stale_patch = await firm.request(
        "PATCH", _path(case["id"], applicant["id"]), json={"version": 9, "first_name": "Z"}
    )
    assert stale_patch.status_code == 409
    stale_delete = await firm.request(
        "DELETE", _path(case["id"], applicant["id"]), params={"version": 9}
    )
    assert stale_delete.status_code == 409


async def test_applicant_from_other_case_is_404(firm: Firm) -> None:
    case_a = await firm.create_case()
    case_b = await firm.create_case()
    applicant = await firm.add_applicant(case_a["id"])
    resp = await firm.request("GET", _path(case_b["id"], applicant["id"]))
    assert resp.status_code == 404


async def test_applicants_of_unknown_case_is_404(firm: Firm) -> None:
    resp = await firm.request("GET", _path("00000000-0000-7000-8000-000000000000"))
    assert resp.status_code == 404
