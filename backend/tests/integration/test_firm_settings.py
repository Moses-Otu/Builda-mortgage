"""Per-firm settings (Phase 1.1-F)."""

from __future__ import annotations

import pytest

from tests.conftest import Firm, MakeFirm


async def _settings(firm: Firm) -> dict[str, object]:
    resp = await firm.request("GET", "/api/v1/firm/settings")
    assert resp.status_code == 200
    assert resp.headers["etag"] == f'"{resp.json()["version"]}"'
    body: dict[str, object] = resp.json()
    return body


async def test_new_firm_has_platform_defaults(firm: Firm) -> None:
    body = await _settings(firm)
    assert body["max_applicants"] == 4
    assert body["require_four_eyes_verification"] is False
    assert body["platform_max_applicants"] == 8


async def test_firm_limit_governs_applicants(firm: Firm) -> None:
    resp = await firm.request(
        "PATCH", "/api/v1/firm/settings", if_match=1, json={"max_applicants": 2}
    )
    assert resp.status_code == 200, resp.text
    case = await firm.create_case()
    await firm.add_applicant(case["id"])
    await firm.add_applicant(case["id"])
    third = await firm.request(
        "POST", f"/api/v1/cases/{case['id']}/applicants", json={"first_name": "C", "last_name": "D"}
    )
    assert third.status_code == 422
    assert "at most 2" in third.json()["detail"]


async def test_limit_is_per_firm(firm: Firm, make_firm: MakeFirm) -> None:
    other = await make_firm()
    await firm.request("PATCH", "/api/v1/firm/settings", if_match=1, json={"max_applicants": 1})
    assert (await _settings(other))["max_applicants"] == 4
    case = await other.create_case()
    for _ in range(4):
        await other.add_applicant(case["id"])


@pytest.mark.parametrize("value", [0, 9])
async def test_limit_within_platform_bounds(firm: Firm, value: int) -> None:
    resp = await firm.request(
        "PATCH", "/api/v1/firm/settings", if_match=1, json={"max_applicants": value}
    )
    assert resp.status_code == 422


async def test_only_firm_admin_changes_settings(firm: Firm) -> None:
    adviser = await firm.add_member("adviser")
    resp = await firm.request(
        "PATCH",
        "/api/v1/firm/settings",
        as_=adviser,
        if_match=1,
        json={"require_four_eyes_verification": True},
    )
    assert resp.status_code == 403
    assert (await firm.request("GET", "/api/v1/firm/settings", as_=adviser)).status_code == 200


async def test_settings_change_is_audited_and_versioned(firm: Firm) -> None:
    resp = await firm.request(
        "PATCH",
        "/api/v1/firm/settings",
        if_match=1,
        json={"require_four_eyes_verification": True},
    )
    assert resp.json()["version"] == 2
    stale = await firm.request(
        "PATCH", "/api/v1/firm/settings", if_match=1, json={"max_applicants": 3}
    )
    assert stale.status_code == 412
    noop = await firm.request(
        "PATCH",
        "/api/v1/firm/settings",
        if_match=2,
        json={"require_four_eyes_verification": True},
    )
    assert noop.status_code == 422
