"""Stage 3-A: the financial profile (rule ``profile/1``) and the provenance of every figure.

SYNTHETIC payslips only. The profile is read-only: every current (not retired) figure from
items that are not withdrawn, labelled by origin, each traced to its source."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import asyncpg
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from meip.core.config import Settings
from meip.core.db import create_engine, create_sessionmaker
from meip.documents.processing import run_once
from meip.documents.storage import FilesystemBlobStore
from tests.conftest import TEST_DB, Firm, db_url
from tests.synthetic_docs import p60, payslip_series

Worker = tuple[async_sessionmaker[AsyncSession], FilesystemBlobStore, Settings]
SECTIONS = ("summary", "payments", "deductions", "year_to_date")


@pytest.fixture
async def worker(settings: Settings, blob_root: Path) -> AsyncIterator[Worker]:
    engine = create_engine(settings.database_url, pool_size=4)
    try:
        yield create_sessionmaker(engine), FilesystemBlobStore(blob_root), settings
    finally:
        await engine.dispose()


@pytest.fixture
async def owner() -> AsyncIterator[asyncpg.Connection]:
    conn = await asyncpg.connect(db_url("meip_owner", TEST_DB, driver="postgresql"))
    try:
        await conn.execute(
            "UPDATE jobs SET status = 'done', locked_until = NULL "
            "WHERE status IN ('queued', 'running')"
        )
        yield conn
    finally:
        await conn.close()


class Case:
    def __init__(self, firm: Firm, case: dict[str, Any], alex: dict[str, Any], source: str):
        self.firm, self.case, self.alex, self.source = firm, case, alex, source
        self.base = f"/api/v1/cases/{case['id']}"

    async def upload(self, worker: Worker, pdf: bytes) -> dict[str, Any]:
        resp = await self.firm.upload(self.case["id"], pdf, applicant_id=self.alex["id"])
        assert resp.status_code == 201, resp.text
        while await run_once(*worker):
            pass
        doc: dict[str, Any] = (
            await self.firm.request("GET", f"{self.base}/documents/{resp.json()['id']}")
        ).json()
        return doc

    async def review(
        self, doc: dict[str, Any], overrides: dict[str, dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        """Accept every field, except those whose field code is in ``overrides``."""
        url = f"{self.base}/documents/{doc['id']}"
        x = (await self.firm.request("GET", f"{url}/extraction")).json()
        overrides = overrides or {}
        decisions = [
            {"ordinal": f["ordinal"], **overrides.get(f["field_code"], {"decision": "accept"})}
            for s in SECTIONS
            for f in x[s]
        ]
        resp = await self.firm.request(
            "POST",
            f"{url}/review",
            if_match=doc["version"],
            json={"income_source_id": self.source, "frequency": "monthly", "decisions": decisions},
        )
        assert resp.status_code == 201, resp.text
        out: dict[str, Any] = x
        return out

    async def declare_basic(self, pounds: int, supersedes: str | None = None) -> str:
        body: dict[str, Any] = {
            "income_source_id": self.source,
            "measure": "basic_pay",
            "value": {"type": "money", "amount_minor": pounds * 100},
            "frequency": "annual",
            "source_kind": "declared",
        }
        if supersedes:
            body["supersedes_observation_id"] = supersedes
        resp = await self.firm.request("POST", f"{self.base}/financial/observations", json=body)
        assert resp.status_code == 201, resp.text
        return str(resp.json()["id"])

    async def profile(self, as_: str | None = None) -> dict[str, Any]:
        resp = await self.firm.request("GET", f"{self.base}/profile", as_=as_)
        assert resp.status_code == 200, resp.text
        out: dict[str, Any] = resp.json()
        return out


@pytest.fixture
async def case(firm: Firm, owner: asyncpg.Connection) -> Case:
    c = await firm.create_case()
    alex = await firm.add_applicant(c["id"], first_name="Alex", last_name="Example")
    source = await firm.request(
        "POST",
        f"/api/v1/cases/{c['id']}/financial/income-sources",
        json={"applicant_id": alex["id"], "kind": "employment"},
    )
    assert source.status_code == 201, source.text
    return Case(firm, c, alex, source.json()["id"])


def field(x: dict[str, Any], code: str) -> dict[str, Any]:
    return next(f for s in SECTIONS for f in x[s] if f["field_code"] == code)


async def test_every_origin_with_its_provenance(case: Case, worker: Worker) -> None:
    june, july = payslip_series(months=2)
    first = await case.upload(worker, june.document.pdf())
    net_extracted = june.net_minor
    x = await case.review(
        first,
        {
            "net_pay": {
                "decision": "correct",
                "corrected_money_minor": net_extracted - 100,
                "reason": "Paper copy shows £1 less",
            },
            "ytd_gross_pay": {"decision": "reject", "reason": "Smudged"},
        },
    )
    second = await case.upload(worker, july.document.pdf())
    await case.review(second)
    unrecognised = await case.upload(worker, p60().pdf())
    assert unrecognised["status"] == "unrecognised"
    old = await case.declare_basic(45_000)
    await case.declare_basic(46_000, supersedes=old)

    profile = await case.profile()
    assert profile["rules"] == "profile/1"
    assert len(profile["fingerprint"]) == 64
    assert profile["case"]["id"] == case.case["id"]
    [applicant] = profile["applicants"]
    assert applicant["first_name"] == "Alex"
    assert "date_of_birth" not in applicant  # only what the profile needs
    figures = profile["figures"]
    assert all(f["item_id"] == case.source for f in figures)

    # Corrected: the extracted value, the correction, the reason, the exact field and file.
    [net] = [
        f
        for f in figures
        if f["measure"] == "net_pay" and f["evidence"]["document_id"] == first["id"]
    ]
    assert net["origin"] == "corrected"
    assert net["value"]["amount_minor"] == net_extracted - 100
    e = net["evidence"]
    assert e["decision"] == "correct"
    assert e["correction_reason"] == "Paper copy shows £1 less"
    assert e["field"]["value_money_minor"] == net_extracted
    assert e["field"]["ordinal"] == field(x, "net_pay")["ordinal"]
    assert e["field"]["value_evidence"] == field(x, "net_pay")["value_evidence"]
    assert e["sha256"] == first["original"]["sha256"]
    assert e["document_version_id"] == first["original"]["id"]
    assert e["schema_version"] == "payslip/1.0.0"
    assert e["reviewed_by"] is not None
    assert e["reviewed_at"]
    # The arithmetic check cites the corrected net pay.
    arith = {
        f["key"]
        for f in profile["checks"]["findings"]
        if f["rule_code"] == "PAY-ARITH"
        and any(ev.get("document_id") == first["id"] for ev in f["evidence"])
    }
    assert arith
    assert arith <= set(net["cited_by"])

    # Extracted: accepted as read. The rejected field is never a figure.
    july_figures = [
        f for f in figures if f["evidence"] and f["evidence"]["document_id"] == second["id"]
    ]
    assert july_figures
    assert {f["origin"] for f in july_figures} == {"extracted"}
    measures_june = {
        f["measure"]
        for f in figures
        if f["evidence"] and f["evidence"]["document_id"] == first["id"]
    }
    assert "ytd_gross_pay" not in measures_june

    # Declared: the current value only, with the value it replaced in its history.
    [declared] = [f for f in figures if f["origin"] == "declared"]
    assert declared["value"]["amount_minor"] == 4_600_000
    assert declared["evidence"] is None
    assert [h["value"]["amount_minor"] for h in declared["history"]] == [4_500_000]
    assert declared["history"][0]["retired_reason"] == "superseded"

    # The inventory lists every document, including the one not relied on.
    statuses = {d["id"]: d["status"] for d in profile["documents"]}
    assert statuses == {
        first["id"]: "reviewed",
        second["id"]: "reviewed",
        unrecognised["id"]: "unrecognised",
    }


async def test_the_fingerprint_is_stable_and_follows_any_change(case: Case, worker: Worker) -> None:
    [slip] = payslip_series(months=1)
    doc = await case.upload(worker, slip.document.pdf())
    await case.review(doc)
    first = await case.profile()
    assert (await case.profile())["fingerprint"] == first["fingerprint"]

    declared = await case.declare_basic(45_000)
    after_declaring = await case.profile()
    assert after_declaring["fingerprint"] != first["fingerprint"]

    # Retiring a figure removes it from the profile and changes the fingerprint.
    resp = await case.firm.request(
        "POST",
        f"{case.base}/financial/observations/{declared}/retirement",
        json={"reason": "entered_in_error"},
    )
    assert resp.status_code == 200, resp.text
    after_retiring = await case.profile()
    assert declared not in {f["id"] for f in after_retiring["figures"]}
    assert after_retiring["fingerprint"] != after_declaring["fingerprint"]


async def test_a_withdrawn_items_figures_are_not_in_the_profile(case: Case, worker: Worker) -> None:
    [slip] = payslip_series(months=1)
    doc = await case.upload(worker, slip.document.pdf())
    await case.review(doc)
    assert (await case.profile())["figures"]
    sources = (await case.firm.request("GET", f"{case.base}/financial/income-sources")).json()
    resp = await case.firm.request(
        "POST",
        f"{case.base}/financial/income-sources/{case.source}/withdrawal",
        if_match=sources[0]["version"],
        json={"reason": "entered_in_error"},
    )
    assert resp.status_code == 200, resp.text
    profile = await case.profile()
    assert profile["figures"] == []
    assert profile["income_sources"] == []
    assert [d["id"] for d in profile["documents"]] == [doc["id"]]  # still listed
