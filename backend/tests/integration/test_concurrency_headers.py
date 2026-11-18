"""Optimistic concurrency over HTTP: ETag on reads and writes, If-Match on mutations."""

from __future__ import annotations

import pytest

from tests.conftest import Firm


async def test_single_resources_carry_an_etag(firm: Firm) -> None:
    created = await firm.request("POST", "/api/v1/cases", json={"purpose": "purchase"})
    assert created.headers["etag"] == '"1"'
    case_id = created.json()["id"]
    fetched = await firm.request("GET", f"/api/v1/cases/{case_id}")
    assert fetched.headers["etag"] == '"1"'
    updated = await firm.request(
        "PATCH", f"/api/v1/cases/{case_id}", if_match=1, json={"term_months": 300}
    )
    assert updated.headers["etag"] == '"2"'
    assert updated.json()["version"] == 2


async def test_mutation_without_if_match_is_428(firm: Firm) -> None:
    case = await firm.create_case()
    resp = await firm.request("PATCH", f"/api/v1/cases/{case['id']}", json={"term_months": 300})
    assert resp.status_code == 428
    assert resp.json()["type"] == "urn:meip:problem:precondition-required"


@pytest.mark.parametrize("value", ["1", 'W/"1"', '"1", "2"', '"abc"', "*"])
async def test_malformed_if_match_is_400(firm: Firm, value: str) -> None:
    case = await firm.create_case()
    resp = await firm.request(
        "PATCH",
        f"/api/v1/cases/{case['id']}",
        json={"term_months": 300},
        headers={"If-Match": value},
    )
    assert resp.status_code == 400
    assert resp.json()["type"] == "urn:meip:problem:bad-request"


async def test_stale_if_match_is_412_and_changes_nothing(firm: Firm) -> None:
    case = await firm.create_case()
    await firm.request(
        "PATCH", f"/api/v1/cases/{case['id']}", if_match=1, json={"term_months": 300}
    )
    stale = await firm.request(
        "PATCH", f"/api/v1/cases/{case['id']}", if_match=1, json={"term_months": 120}
    )
    assert stale.status_code == 412
    current = await firm.request("GET", f"/api/v1/cases/{case['id']}")
    assert current.json()["term_months"] == 300


async def test_version_in_body_is_rejected(firm: Firm) -> None:
    case = await firm.create_case()
    resp = await firm.request(
        "PATCH",
        f"/api/v1/cases/{case['id']}",
        if_match=1,
        json={"version": 1, "term_months": 300},
    )
    assert resp.status_code == 422  # unknown field: version now travels only in If-Match
