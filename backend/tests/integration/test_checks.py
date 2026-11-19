"""Stage 2-A: the payslip checks over real reviewed (SYNTHETIC) payslips, and acknowledgements.

Each test uploads a synthetic payslip series, runs the real worker (ingest, then extract),
reviews every payslip through the API (accept all), and reads the checks.
"""

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
from tests.conftest import TEST_DB, Firm, MakeFirm, db_url
from tests.synthetic_docs import payslip_series

Worker = tuple[async_sessionmaker[AsyncSession], FilesystemBlobStore, Settings]


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
    def __init__(self, firm: Firm, case: dict[str, Any], applicant: dict[str, Any], source: str):
        self.firm, self.case, self.applicant, self.source = firm, case, applicant, source
        self.base = f"/api/v1/cases/{case['id']}"

    async def reviewed_series(self, worker: Worker, **series: Any) -> list[dict[str, Any]]:
        """Upload and review (accept all) each payslip of a synthetic series, in order."""
        docs = []
        for slip in payslip_series(**series):
            upload = await self.firm.upload(
                self.case["id"], slip.document.pdf(), applicant_id=self.applicant["id"]
            )
            assert upload.status_code == 201, upload.text
            while await run_once(*worker):
                pass
            url = f"{self.base}/documents/{upload.json()['id']}"
            doc = (await self.firm.request("GET", url)).json()
            assert doc["status"] == "extracted", doc
            extraction = (await self.firm.request("GET", f"{url}/extraction")).json()
            ordinals = [
                f["ordinal"]
                for section in ("summary", "payments", "deductions", "year_to_date")
                for f in extraction[section]
            ]
            review = await self.firm.request(
                "POST",
                f"{url}/review",
                if_match=doc["version"],
                json={
                    "income_source_id": self.source,
                    "frequency": "monthly",
                    "decisions": [{"ordinal": o, "decision": "accept"} for o in ordinals],
                },
            )
            assert review.status_code == 201, review.text
            docs.append(doc)
        return docs

    async def declare_basic(self, annual_pounds: int, supersedes: str | None = None) -> str:
        body: dict[str, Any] = {
            "income_source_id": self.source,
            "measure": "basic_pay",
            "value": {"type": "money", "amount_minor": annual_pounds * 100},
            "frequency": "annual",
            "source_kind": "declared",
        }
        if supersedes:
            body["supersedes_observation_id"] = supersedes
        resp = await self.firm.request("POST", f"{self.base}/financial/observations", json=body)
        assert resp.status_code == 201, resp.text
        return str(resp.json()["id"])

    async def checks(self, as_: str | None = None) -> dict[str, Any]:
        resp = await self.firm.request("GET", f"{self.base}/checks", as_=as_)
        assert resp.status_code == 200, resp.text
        out: dict[str, Any] = resp.json()
        return out

    async def acknowledge(self, key: str, note: str = "Seen; asked the client") -> Any:
        return await self.firm.request(
            "POST", f"{self.base}/checks/{key}/acknowledgement", json={"note": note}
        )


@pytest.fixture
async def case(firm: Firm, owner: asyncpg.Connection) -> Case:
    c = await firm.create_case()
    applicant = await firm.add_applicant(c["id"])
    source = await firm.request(
        "POST",
        f"/api/v1/cases/{c['id']}/financial/income-sources",
        json={"applicant_id": applicant["id"], "kind": "employment"},
    )
    assert source.status_code == 201, source.text
    return Case(firm, c, applicant, source.json()["id"])


def by_rule(checks: dict[str, Any], rule: str) -> list[dict[str, Any]]:
    return [f for f in checks["findings"] if f["rule_code"] == rule]


def statuses(checks: dict[str, Any], rule: str) -> list[str]:
    return [f["status"] for f in by_rule(checks, rule)]


# --- the checks over real reviewed payslips ------------------------------------------------------


async def test_a_clean_series_passes_every_check(case: Case, worker: Worker) -> None:
    await case.reviewed_series(worker, months=4)
    await case.declare_basic(45_000)
    checks = await case.checks()
    assert checks["ruleset_version"] == "payslip-checks/1"
    assert checks["parameters"]["declared_basic_threshold_pct"] == "2.0"
    assert statuses(checks, "PAY-ARITH") == ["passed"] * 4
    for rule in ("PERIOD-GAP", "PERIOD-DUP", "YTD-GROSS", "YTD-TAX", "DECLARED-BASIC"):
        assert statuses(checks, rule) == ["passed"], rule
    assert all(f["status"] == "passed" for f in checks["findings"])


async def test_evidence_resolves_to_the_payslip_and_field(case: Case, worker: Worker) -> None:
    docs = await case.reviewed_series(worker, months=2)
    [arith, *_] = by_rule(await case.checks(), "PAY-ARITH")
    labels = {e["label"] for e in arith["evidence"]}
    assert labels == {"Total Gross Pay", "Total Deductions", "Net Pay"}
    assert {e["document_id"] for e in arith["evidence"]} == {docs[0]["id"]}
    assert all(e["field_ordinal"] is not None for e in arith["evidence"])
    assert arith["evidence"][0]["document_label"] == "May 2026 payslip"


@pytest.mark.parametrize(
    ("faults", "rule", "status", "words"),
    [
        ({"missing_month"}, "PERIOD-GAP", "discrepancy", "1 Jul 2026 to 31 Jul 2026"),
        ({"ytd_break"}, "YTD-GROSS", "discrepancy", "Difference £375.00"),
        ({"arithmetic_error"}, "PAY-ARITH", "discrepancy", "Difference £10.00"),
        ({"duplicate_period"}, "PERIOD-DUP", "discrepancy", "same time"),
        ({"adjustment"}, "YTD-GROSS", "not_run", "Arrears Adjustment"),
        ({"taxable_label"}, "YTD-GROSS", "not_run", "taxable pay excludes pension"),
        ({"no_basic"}, "DECLARED-BASIC", "not_run", "no payslip shows a basic pay line"),
        ({"part_period"}, "DECLARED-BASIC", "not_run", "Unpaid Leave"),
    ],
)
async def test_each_fault_gives_exactly_the_expected_finding(
    case: Case, worker: Worker, faults: set[str], rule: str, status: str, words: str
) -> None:
    fault_at = 3 if "part_period" in faults else 2  # part period: on the latest payslip
    await case.reviewed_series(worker, months=4, faults=frozenset(faults), fault_at=fault_at)
    await case.declare_basic(45_000)
    checks = await case.checks()
    flagged = [f for f in checks["findings"] if f["status"] != "passed"]
    assert any(f["rule_code"] == rule and f["status"] == status for f in flagged), flagged
    assert any(words in f["message"] or words in f["title"] for f in by_rule(checks, rule))
    if status == "discrepancy":
        assert [f["rule_code"] for f in flagged if f["status"] == "discrepancy"] == [rule]


async def test_period_ends_only_still_judge_coverage(case: Case, worker: Worker) -> None:
    await case.reviewed_series(worker, months=3, faults=frozenset({"period_end_only"}))
    checks = await case.checks()
    assert statuses(checks, "PERIOD-GAP") == ["passed"]
    assert statuses(checks, "YTD-GROSS") == ["passed"]


async def test_declared_basic_ten_percent_out_is_a_discrepancy(case: Case, worker: Worker) -> None:
    await case.reviewed_series(worker, months=2)
    await case.declare_basic(50_000)
    [f] = by_rule(await case.checks(), "DECLARED-BASIC")
    assert f["status"] == "discrepancy"
    assert f["amounts"] == {"expected": 5_000_000, "actual": 4_500_000, "difference": -500_000}
    assert {e["source_kind"] for e in f["evidence"]} == {"declared", "document"}


async def test_rejected_fields_are_never_used(case: Case, worker: Worker, firm: Firm) -> None:
    # Review the payslip rejecting net pay: the arithmetic check can't run, it doesn't guess.
    slip = payslip_series(months=1)[0]
    upload = await firm.upload(
        case.case["id"], slip.document.pdf(), applicant_id=case.applicant["id"]
    )
    while await run_once(*worker):
        pass
    url = f"{case.base}/documents/{upload.json()['id']}"
    doc = (await firm.request("GET", url)).json()
    extraction = (await firm.request("GET", f"{url}/extraction")).json()
    fields = [
        f for s in ("summary", "payments", "deductions", "year_to_date") for f in extraction[s]
    ]
    decisions = [
        {"ordinal": f["ordinal"], "decision": "reject", "reason": "Unreadable"}
        if f["field_code"] == "net_pay"
        else {"ordinal": f["ordinal"], "decision": "accept"}
        for f in fields
    ]
    review = await firm.request(
        "POST",
        f"{url}/review",
        if_match=doc["version"],
        json={"income_source_id": case.source, "decisions": decisions},
    )
    assert review.status_code == 201, review.text
    [f] = by_rule(await case.checks(), "PAY-ARITH")
    assert f["status"] == "not_run"
    assert "net pay" in f["message"]


# --- acknowledgements ------------------------------------------------------------


async def _discrepancy(case: Case, worker: Worker) -> dict[str, Any]:
    await case.reviewed_series(worker, months=3, faults=frozenset({"arithmetic_error"}), fault_at=1)
    found: list[dict[str, Any]] = [
        f for f in (await case.checks())["findings"] if f["status"] == "discrepancy"
    ]
    assert len(found) == 1, found
    return found[0]


async def test_acknowledging_keeps_it_a_discrepancy(case: Case, worker: Worker) -> None:
    found = await _discrepancy(case, worker)
    resp = await case.acknowledge(found["key"], "Employer confirmed a payroll error")
    assert resp.status_code == 201, resp.text
    out = resp.json()
    assert out["status"] == "discrepancy"
    assert out["acknowledgement"]["note"] == "Employer confirmed a payroll error"
    again = [f for f in (await case.checks())["findings"] if f["key"] == found["key"]]
    assert again[0]["status"] == "discrepancy"
    assert again[0]["acknowledgement"]["note"] == "Employer confirmed a payroll error"


async def test_acknowledgement_refusals(case: Case, worker: Worker) -> None:
    found = await _discrepancy(case, worker)
    passed = next(f for f in (await case.checks())["findings"] if f["status"] == "passed")
    assert (await case.acknowledge(found["key"])).status_code == 201

    duplicate = await case.acknowledge(found["key"])
    assert duplicate.status_code == 409
    assert duplicate.json()["type"] == "urn:meip:problem:already-acknowledged"

    for key in ("f" * 64, passed["key"]):
        stale = await case.acknowledge(key)
        assert stale.status_code == 409, stale.text
        assert stale.json()["type"] == "urn:meip:problem:finding-not-current"

    assert (await case.acknowledge(found["key"], "   ")).status_code == 422
    assert (await case.acknowledge(found["key"], "x" * 1001)).status_code == 422
    assert (await case.acknowledge("NOT-A-KEY")).status_code == 422


async def test_acknowledging_changes_nothing_else(
    case: Case, worker: Worker, owner: asyncpg.Connection
) -> None:
    found = await _discrepancy(case, worker)
    tables = (
        "financial_observations",
        "income_sources",
        "document_reviews",
        "field_reviews",
        "documents",
        "document_versions",
        "extracted_fields",
        "cases",
        "applicants",
    )

    async def snapshot() -> dict[str, list[str]]:
        out = {}
        for table in tables:
            rows = await owner.fetch(
                f"SELECT to_jsonb(t)::text AS row FROM {table} t "  # noqa: S608 # fixed names
                "WHERE tenant_id = $1 ORDER BY id",
                case.firm.tenant_id,
            )
            out[table] = [r["row"] for r in rows]
        return out

    before = await snapshot()
    assert (await case.acknowledge(found["key"])).status_code == 201
    assert await snapshot() == before


async def test_acknowledgement_is_audited_with_the_note_erasable(
    case: Case, worker: Worker
) -> None:
    found = await _discrepancy(case, worker)
    await case.acknowledge(found["key"], "A distinctive broker note")
    events = (await case.firm.request("GET", f"{case.base}/events?limit=100")).json()["items"]
    [event] = [e for e in events if e["action"] == "check.acknowledged"]
    assert event["changes"]["rule_code"] == "PAY-ARITH"
    assert event["changes"]["finding_key"] == found["key"]
    assert event["changes"]["note"] == {"changed": True}
    assert event["details"]["note"] == "A distinctive broker note"


async def test_a_changed_input_gives_a_new_unacknowledged_finding(
    case: Case, worker: Worker
) -> None:
    await case.reviewed_series(worker, months=2)
    declared = await case.declare_basic(50_000)
    [first] = by_rule(await case.checks(), "DECLARED-BASIC")
    assert first["status"] == "discrepancy"
    assert (await case.acknowledge(first["key"])).status_code == 201

    await case.declare_basic(48_000, supersedes=declared)  # still more than 2 % out
    [second] = by_rule(await case.checks(), "DECLARED-BASIC")
    assert second["status"] == "discrepancy"
    assert second["key"] != first["key"]
    assert second["acknowledgement"] is None  # the old note doesn't cover the new figure
    assert (await case.acknowledge(first["key"])).status_code == 409  # no longer current


async def test_retiring_a_cited_figure_replaces_the_finding(case: Case, worker: Worker) -> None:
    found = await _discrepancy(case, worker)
    await case.acknowledge(found["key"])
    net = next(e for e in found["evidence"] if e["measure"] == "net_pay")
    retire = await case.firm.request(
        "POST",
        f"{case.base}/financial/observations/{net['observation_id']}/retirement",
        json={"reason": "entered_in_error"},
    )
    assert retire.status_code == 200, retire.text
    checks = await case.checks()
    assert found["key"] not in {f["key"] for f in checks["findings"]}
    assert all(f["acknowledgement"] is None for f in checks["findings"])


# --- access ----------------------------------------------------------------------


async def test_read_only_can_read_but_not_acknowledge(case: Case, worker: Worker) -> None:
    found = await _discrepancy(case, worker)
    reader = await case.firm.add_member("read_only")
    assert (await case.checks(as_=reader))["findings"]
    resp = await case.firm.request(
        "POST",
        f"{case.base}/checks/{found['key']}/acknowledgement",
        as_=reader,
        json={"note": "x"},
    )
    assert resp.status_code == 403


async def test_another_firm_sees_nothing(case: Case, worker: Worker, make_firm: MakeFirm) -> None:
    found = await _discrepancy(case, worker)
    other = await make_firm("Other Firm")
    assert (await other.request("GET", f"{case.base}/checks")).status_code == 404
    resp = await other.request(
        "POST", f"{case.base}/checks/{found['key']}/acknowledgement", json={"note": "x"}
    )
    assert resp.status_code == 404
