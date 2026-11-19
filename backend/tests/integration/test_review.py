"""Step 2-D: broker review of an extracted payslip, and saving the approved figures.

All documents are SYNTHETIC. Each test uploads a synthetic payslip, runs the real worker
(ingest, then extract) and reviews the result through the API.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path
from typing import Any

import asyncpg
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from structlog.testing import capture_logs

from meip.audit import service as audit_service
from meip.core.config import Settings
from meip.core.db import create_engine, create_sessionmaker
from meip.documents.processing import run_once
from meip.documents.storage import FilesystemBlobStore
from tests.conftest import TEST_DB, Firm, db_url
from tests.synthetic_docs import NON_PAYSLIPS, PAYSLIPS

Worker = tuple[async_sessionmaker[AsyncSession], FilesystemBlobStore, Settings]
FIGURES = {
    "gross_pay",
    "net_pay",
    "basic_pay",
    "overtime",
    "commission",
    "bonus",
    "allowance",
    "income_tax",
    "national_insurance",
    "pension_contribution",
    "student_loan_deduction",
    "ytd_gross_pay",
    "ytd_income_tax",
}


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


class Setup:
    def __init__(self, firm: Firm, case: dict[str, Any], source: dict[str, Any]) -> None:
        self.firm, self.case, self.source = firm, case, source
        self.base = f"/api/v1/cases/{case['id']}"

    async def payslip(
        self, worker: Worker, name: str = "classic", pdf: bytes | None = None
    ) -> dict[str, Any]:
        content = pdf if pdf is not None else PAYSLIPS[name]()[0].pdf()
        resp = await self.firm.upload(
            self.case["id"], content, applicant_id=self.case["applicant"]["id"]
        )
        assert resp.status_code == 201, resp.text
        while await run_once(*worker):
            pass
        doc: dict[str, Any] = (
            await self.firm.request("GET", f"{self.base}/documents/{resp.json()['id']}")
        ).json()
        return doc

    async def extraction(self, doc: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = (
            await self.firm.request("GET", f"{self.base}/documents/{doc['id']}/extraction")
        ).json()
        return out

    async def review(
        self, doc: dict[str, Any], decisions: list[dict[str, Any]], **body: Any
    ) -> Any:
        payload = {"income_source_id": self.source["id"], "decisions": decisions, **body}
        if_match = body.pop("if_match", None) if "if_match" in body else doc["version"]
        payload.pop("if_match", None)
        return await self.firm.request(
            "POST", f"{self.base}/documents/{doc['id']}/review", json=payload, if_match=if_match
        )


def fields_of(extraction: dict[str, Any]) -> list[dict[str, Any]]:
    fields: list[dict[str, Any]] = (
        extraction["summary"]
        + extraction["payments"]
        + extraction["deductions"]
        + extraction["year_to_date"]
    )
    return fields


def accept_all(extraction: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"ordinal": f["ordinal"], "decision": "accept"} for f in fields_of(extraction)]


def by_code(extraction: dict[str, Any], code: str) -> dict[str, Any]:
    return next(f for f in fields_of(extraction) if f["field_code"] == code)


@pytest.fixture
async def setup(firm: Firm, owner: asyncpg.Connection) -> Setup:
    case = await firm.create_case()
    case["applicant"] = await firm.add_applicant(case["id"])
    source = await firm.request(
        "POST",
        f"/api/v1/cases/{case['id']}/financial/income-sources",
        json={"applicant_id": case["applicant"]["id"], "kind": "employment"},
    )
    assert source.status_code == 201, source.text
    return Setup(firm, case, source.json())


async def _observations(s: Setup) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = (
        await s.firm.request("GET", f"{s.base}/financial/observations")
    ).json()
    return [o for o in items if o["source_kind"] == "document"]


# --- the happy path, all three layouts ----------------------------------------------------


@pytest.mark.parametrize(
    ("name", "frequency", "sent"),
    [
        ("classic", "monthly", None),
        ("earnings", "monthly", "monthly"),
        ("two-pages", "weekly", None),
    ],
)
async def test_accept_all_saves_only_financial_figures_with_full_provenance(
    setup: Setup, worker: Worker, name: str, frequency: str, sent: str | None
) -> None:
    doc = await setup.payslip(worker, name)
    extraction = await setup.extraction(doc)
    extra = {"frequency": sent} if sent else {}
    resp = await setup.review(doc, accept_all(extraction), **extra)
    assert resp.status_code == 201, resp.text
    review = resp.json()
    assert review["frequency"] == frequency

    # Only financial fields become figures; descriptive fields are reviewed evidence.
    for d in review["decisions"]:
        assert (d["observation_id"] is not None) == (d["field_code"] in FIGURES), d
    saved = await _observations(setup)
    assert len(saved) == sum(1 for d in review["decisions"] if d["observation_id"])
    extracted = {f["ordinal"]: f for f in fields_of(extraction)}
    decisions = {d["observation_id"]: d for d in review["decisions"] if d["observation_id"]}
    for o in saved:
        d = decisions[o["id"]]
        f = extracted[d["ordinal"]]
        assert o["measure"] == f["field_code"]
        assert o["value"]["amount_minor"] == f["value_money_minor"]  # the extracted value
        assert o["field_review_id"] is not None
        assert o["recorded_by_user_id"] is not None
        if f["field_code"].startswith("ytd_"):
            assert (o["frequency"], o["as_of_date"]) == (
                None,
                by_code(extraction, "pay_date")["value_date"],
            )
        else:
            assert o["frequency"] == frequency
    document = (await setup.firm.request("GET", f"{setup.base}/documents/{doc['id']}")).json()
    assert document["status"] == "reviewed"


async def test_provenance_resolves_back_to_the_original(
    setup: Setup, worker: Worker, owner: asyncpg.Connection
) -> None:
    synthetic, _ = PAYSLIPS["classic"]()
    pdf = synthetic.pdf()
    doc = await setup.payslip(worker, pdf=pdf)
    resp = await setup.review(doc, accept_all(await setup.extraction(doc)))
    net = next(o for o in await _observations(setup) if o["measure"] == "net_pay")
    chain = await owner.fetchrow(
        "SELECT fr.decision, f.raw_text, f.page_number, f.x0, r.provider, v.sha256"
        " FROM financial_observations o"
        " JOIN field_reviews fr ON fr.id = o.field_review_id"
        " JOIN extracted_fields f ON f.id = fr.field_id"
        " JOIN extraction_runs r ON r.id = f.run_id"
        " JOIN document_versions v ON v.id = r.document_version_id"
        " WHERE o.id = $1",
        uuid.UUID(net["id"]),
    )
    assert chain is not None
    assert (chain["decision"], chain["raw_text"], chain["provider"]) == (
        "accept",
        "£2,451.96",
        "synthetic-fixture",
    )
    assert chain["sha256"] == hashlib.sha256(pdf).hexdigest()
    assert resp.json()["income_source_id"] == setup.source["id"]


# --- corrections, rejections, assigned measures ---------------------------------------------


async def test_corrections_keep_the_original_and_rejections_save_nothing(
    setup: Setup, worker: Worker
) -> None:
    doc = await setup.payslip(worker)
    extraction = await setup.extraction(doc)
    decisions = accept_all(extraction)
    net, tax, pay_date = (
        by_code(extraction, c)["ordinal"] for c in ("net_pay", "income_tax", "pay_date")
    )
    for d in decisions:
        if d["ordinal"] == net:
            d.update(decision="correct", corrected_money_minor=245000, reason="Smudged digit")
        if d["ordinal"] == tax:
            d.update(decision="reject", reason="Belongs to another period")
        if d["ordinal"] == pay_date:
            d.update(decision="correct", corrected_date="2026-01-30", reason="Bank holiday")
    resp = await setup.review(doc, decisions)
    assert resp.status_code == 201, resp.text

    saved = {o["measure"]: o for o in await _observations(setup)}
    assert saved["net_pay"]["value"]["amount_minor"] == 245000  # the correction
    assert "income_tax" not in saved  # rejected
    assert saved["ytd_gross_pay"]["as_of_date"] == "2026-01-30"  # corrected pay date used

    after = await setup.extraction(doc)
    assert by_code(after, "net_pay")["value_money_minor"] == 245196  # field unchanged
    review = {d["ordinal"]: d for d in resp.json()["decisions"]}
    assert (review[net]["extracted_money_minor"], review[net]["corrected_money_minor"]) == (
        245196,
        245000,
    )
    assert review[net]["reason"] == "Smudged digit"
    assert review[tax]["observation_id"] is None


async def test_unrecognised_line_saved_only_under_an_assigned_measure(
    setup: Setup, worker: Worker
) -> None:
    doc = await setup.payslip(worker, "earnings")
    extraction = await setup.extraction(doc)
    line = next(f for f in extraction["payments"] if f["field_code"] is None)
    assert line["label_text"] == "Unpaid Leave Adjustment"

    def with_line(**change: Any) -> list[dict[str, Any]]:
        decisions = accept_all(extraction)
        next(d for d in decisions if d["ordinal"] == line["ordinal"]).update(change)
        return decisions

    # Not assigned: reviewed, not saved.
    negative = await setup.review(doc, with_line(measure="basic_pay"), frequency="monthly")
    assert negative.status_code == 422  # -120.50 can't be a basic pay figure
    inapplicable = await setup.review(
        doc,
        with_line(
            decision="correct", corrected_money_minor=12050, reason="Refund", measure="net_profit"
        ),
        frequency="monthly",
    )
    assert inapplicable.status_code == 422
    ok = await setup.review(
        doc,
        with_line(
            decision="correct", corrected_money_minor=12050, reason="Refund", measure="overtime"
        ),
        frequency="monthly",
    )
    assert ok.status_code == 201, ok.text
    saved = next(d for d in ok.json()["decisions"] if d["ordinal"] == line["ordinal"])
    assert (saved["measure"], saved["observation_id"] is not None) == ("overtime", True)


async def test_bonus_and_allowance_get_a_qualifier(setup: Setup, worker: Worker) -> None:
    doc = await setup.payslip(worker, "earnings")
    extraction = await setup.extraction(doc)
    decisions = accept_all(extraction)
    bonus = by_code(extraction, "bonus")["ordinal"]
    next(d for d in decisions if d["ordinal"] == bonus)["qualifier"] = "discretionary"
    resp = await setup.review(doc, decisions, frequency="monthly")
    assert resp.status_code == 201, resp.text
    saved = {o["measure"]: o for o in await _observations(setup)}
    assert saved["bonus"]["qualifier"] == "discretionary"


# --- atomicity ----------------------------------------------------------------------------


async def _nothing_saved(s: Setup, doc: dict[str, Any]) -> None:
    document = (await s.firm.request("GET", f"{s.base}/documents/{doc['id']}")).json()
    assert document["status"] == "extracted"
    assert await _observations(s) == []
    assert (
        await s.firm.request("GET", f"{s.base}/documents/{doc['id']}/review")
    ).status_code == 404


async def test_an_invalid_last_decision_saves_nothing(setup: Setup, worker: Worker) -> None:
    doc = await setup.payslip(worker)
    decisions = accept_all(await setup.extraction(doc))
    decisions[-1] = {"ordinal": decisions[-1]["ordinal"], "decision": "reject"}  # no reason
    resp = await setup.review(doc, decisions)
    assert resp.status_code == 422
    await _nothing_saved(setup, doc)


async def test_an_audit_failure_saves_nothing(
    setup: Setup, worker: Worker, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = await setup.payslip(worker)
    decisions = accept_all(await setup.extraction(doc))

    async def failing(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("audit store unavailable")

    monkeypatch.setattr(audit_service, "record", failing)
    resp = await setup.review(doc, decisions)
    monkeypatch.undo()
    assert resp.status_code == 500
    await _nothing_saved(setup, doc)


# --- duplicates and concurrency ----------------------------------------------------------------


async def test_concurrent_submissions_save_one_review(setup: Setup, worker: Worker) -> None:
    doc = await setup.payslip(worker)
    decisions = accept_all(await setup.extraction(doc))
    results = await asyncio.gather(setup.review(doc, decisions), setup.review(doc, decisions))
    statuses = sorted(r.status_code for r in results)
    assert statuses == [201, 409], [r.text for r in results]
    created = next(r for r in results if r.status_code == 201).json()
    refused = next(r for r in results if r.status_code == 409).json()
    assert refused["type"] == "urn:meip:problem:already-reviewed"
    assert refused["existing_review_id"] == created["id"]
    figures = sum(1 for d in created["decisions"] if d["observation_id"])
    assert len(await _observations(setup)) == figures  # one set of figures


async def test_a_retry_after_success_is_409_with_the_review(setup: Setup, worker: Worker) -> None:
    doc = await setup.payslip(worker)
    decisions = accept_all(await setup.extraction(doc))
    first = await setup.review(doc, decisions)
    again = await setup.review(doc, decisions)
    assert (first.status_code, again.status_code) == (201, 409)
    assert again.json()["existing_review_id"] == first.json()["id"]


async def test_a_stale_if_match_is_412(setup: Setup, worker: Worker) -> None:
    doc = await setup.payslip(worker)
    resp = await setup.review(
        doc, accept_all(await setup.extraction(doc)), if_match=doc["version"] - 1
    )
    assert resp.status_code == 412


async def test_the_database_refuses_a_second_review_row(
    setup: Setup, worker: Worker, owner: asyncpg.Connection
) -> None:
    doc = await setup.payslip(worker)
    first = await setup.review(doc, accept_all(await setup.extraction(doc)))
    run_id = await owner.fetchval(
        "SELECT run_id FROM document_reviews WHERE id = $1", uuid.UUID(first.json()["id"])
    )
    conn = await asyncpg.connect(db_url("meip_app", TEST_DB, driver="postgresql"))
    try:
        await conn.execute(
            "SELECT set_config('app.tenant_id', $1, false)", str(setup.firm.tenant_id)
        )
        with pytest.raises(asyncpg.UniqueViolationError):
            await conn.execute(
                "INSERT INTO document_reviews (id, tenant_id, case_id, run_id, income_source_id,"
                " frequency, created_at) VALUES ($1, $2, $3, $4, $5, 'monthly', now())",
                uuid.uuid4(),
                setup.firm.tenant_id,
                uuid.UUID(setup.case["id"]),
                run_id,
                uuid.UUID(setup.source["id"]),
            )
    finally:
        await conn.close()


# --- validation ------------------------------------------------------------------------------


async def test_every_field_needs_exactly_one_decision(setup: Setup, worker: Worker) -> None:
    doc = await setup.payslip(worker)
    decisions = accept_all(await setup.extraction(doc))
    for bad in (
        decisions[:-1],  # missing
        [*decisions, {"ordinal": 999, "decision": "accept"}],  # unknown
        [*decisions, decisions[0]],  # duplicate
    ):
        assert (await setup.review(doc, bad)).status_code == 422
    await _nothing_saved(setup, doc)


@pytest.mark.parametrize(
    ("code", "change"),
    [
        ("net_pay", {"decision": "correct", "corrected_money_minor": 1}),  # no reason
        ("net_pay", {"decision": "correct", "corrected_date": "2026-01-01", "reason": "x"}),
        ("tax_code", {"decision": "accept", "measure": "net_pay"}),  # descriptive field
        ("pay_date", {"decision": "reject", "reason": "Not shown"}),  # no pay date left
        ("net_pay", {"decision": "accept", "qualifier": "unspecified"}),  # not a qualified measure
    ],
    ids=[
        "correction-without-reason",
        "correction-wrong-type",
        "measure-on-descriptive",
        "no-pay-date",
        "bad-qualifier",
    ],
)
async def test_invalid_decisions_are_refused(
    setup: Setup, worker: Worker, code: str, change: dict[str, Any]
) -> None:
    doc = await setup.payslip(worker)
    extraction = await setup.extraction(doc)
    decisions = accept_all(extraction)
    target = by_code(extraction, code)["ordinal"]
    next(d for d in decisions if d["ordinal"] == target).update(change)
    assert (await setup.review(doc, decisions)).status_code == 422
    await _nothing_saved(setup, doc)


async def test_unknown_frequency_needs_to_be_sent(setup: Setup, worker: Worker) -> None:
    doc = await setup.payslip(worker, "earnings")  # no frequency printed
    resp = await setup.review(doc, accept_all(await setup.extraction(doc)))
    assert resp.status_code == 422
    assert "frequency" in resp.json()["detail"]


async def test_income_source_must_be_usable(setup: Setup, worker: Worker, firm: Firm) -> None:
    doc = await setup.payslip(worker)
    decisions = accept_all(await setup.extraction(doc))
    other_case = await firm.create_case()
    other_applicant = await firm.add_applicant(other_case["id"])
    elsewhere = await firm.request(
        "POST",
        f"/api/v1/cases/{other_case['id']}/financial/income-sources",
        json={"applicant_id": other_applicant["id"], "kind": "employment"},
    )
    second = await firm.add_applicant(setup.case["id"])
    theirs = await firm.request(
        "POST",
        f"{setup.base}/financial/income-sources",
        json={"applicant_id": second["id"], "kind": "employment"},
    )
    withdrawn = await firm.request(
        "POST",
        f"{setup.base}/financial/income-sources",
        json={"applicant_id": setup.case["applicant"]["id"], "kind": "employment"},
    )
    gone = await firm.request(
        "POST",
        f"{setup.base}/financial/income-sources/{withdrawn.json()['id']}/withdrawal",
        if_match=1,
        json={"reason": "duplicate"},
    )
    assert gone.status_code == 200, gone.text
    for source in (
        elsewhere.json()["id"],
        theirs.json()["id"],
        withdrawn.json()["id"],
        str(uuid.uuid4()),
    ):
        resp = await setup.review(doc, decisions, income_source_id=source)
        assert resp.status_code in (409, 422), resp.text
    await _nothing_saved(setup, doc)


async def test_only_extracted_documents_can_be_reviewed(setup: Setup, worker: Worker) -> None:
    doc = await setup.payslip(worker, pdf=NON_PAYSLIPS["p60"]().pdf())
    assert doc["status"] == "unrecognised"
    resp = await setup.review(doc, [{"ordinal": 1, "decision": "accept"}])
    assert resp.status_code == 409


# --- database rules -------------------------------------------------------------------------


async def test_lineage_and_immutability_are_enforced(setup: Setup, worker: Worker) -> None:
    doc = await setup.payslip(worker)
    resp = await setup.review(doc, accept_all(await setup.extraction(doc)))
    decision = next(d for d in resp.json()["decisions"] if d["observation_id"])
    field_review_id = (
        await setup.firm.request("GET", f"{setup.base}/financial/observations")
    ).json()
    linked = next(o for o in field_review_id if o["id"] == decision["observation_id"])
    conn = await asyncpg.connect(db_url("meip_app", TEST_DB, driver="postgresql"))
    insert = (
        "INSERT INTO financial_observations (id, tenant_id, case_id, income_source_id, measure,"
        " qualifier_required, value_type, temporal_kind, money_minor, currency, frequency,"
        " period_start, source_kind, recorded_at, field_review_id) VALUES ($1, $2, $3, $4,"
        " 'net_pay', false, 'money', 'flow', 1, 'GBP', 'monthly', $5, $6, now(), $7)"
    )
    args = (setup.firm.tenant_id, uuid.UUID(setup.case["id"]), uuid.UUID(setup.source["id"]))
    try:
        await conn.execute(
            "SELECT set_config('app.tenant_id', $1, false)", str(setup.firm.tenant_id)
        )
        with pytest.raises(asyncpg.CheckViolationError):  # a document figure without lineage
            await conn.execute(insert, uuid.uuid4(), *args, date(2025, 1, 1), "document", None)
        with pytest.raises(asyncpg.CheckViolationError):  # a declared figure claiming lineage
            await conn.execute(
                insert,
                uuid.uuid4(),
                *args,
                date(2025, 2, 1),
                "declared",
                uuid.UUID(linked["field_review_id"]),
            )
        for statement in (
            "UPDATE document_reviews SET frequency = 'weekly'",
            "DELETE FROM document_reviews",
            "UPDATE field_reviews SET reason = 'x'",
            "DELETE FROM field_reviews",
        ):
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.execute(statement)
    finally:
        await conn.close()


async def test_reviews_are_isolated_between_firms(
    setup: Setup, worker: Worker, make_firm: Any
) -> None:
    doc = await setup.payslip(worker)
    await setup.review(doc, accept_all(await setup.extraction(doc)))
    other = await make_firm()
    url = f"{setup.base}/documents/{doc['id']}/review"
    assert (await other.request("GET", url)).status_code == 404
    conn = await asyncpg.connect(db_url("meip_app", TEST_DB, driver="postgresql"))
    try:
        query = (
            "SELECT (SELECT count(*) FROM document_reviews), (SELECT count(*) FROM field_reviews)"
        )
        await conn.execute("SELECT set_config('app.tenant_id', $1, false)", str(other.tenant_id))
        row = await conn.fetchrow(query)
        assert row is not None
        assert (row[0], row[1]) == (0, 0)
        await conn.execute(
            "SELECT set_config('app.tenant_id', $1, false)", str(setup.firm.tenant_id)
        )
        row = await conn.fetchrow(query)
        assert row is not None
        assert row[0] >= 1
        assert row[1] > 0
    finally:
        await conn.close()


# --- audit and logs ---------------------------------------------------------------------------


async def test_one_audit_event_with_corrections_only_in_details(
    setup: Setup, worker: Worker
) -> None:
    doc = await setup.payslip(worker)
    extraction = await setup.extraction(doc)
    decisions = accept_all(extraction)
    net = by_code(extraction, "net_pay")["ordinal"]
    next(d for d in decisions if d["ordinal"] == net).update(
        decision="correct", corrected_money_minor=246802, reason="Kangaroo correction"
    )
    with capture_logs() as logs:
        resp = await setup.review(doc, decisions)
    assert resp.status_code == 201
    events = (await setup.firm.request("GET", f"{setup.base}/events")).json()["items"]
    reviewed = [e for e in events if e["action"] == "document.reviewed"]
    assert len(reviewed) == 1
    event = reviewed[0]
    assert event["changes"]["status"] == {"from": "extracted", "to": "reviewed"}
    assert event["changes"]["correct_count"] == 1
    assert event["changes"]["corrections"] == {"changed": True}
    assert "Kangaroo" not in repr(event["changes"])
    assert "Kangaroo" in repr(event["details"])  # erasable details only
    assert "Kangaroo" not in repr(logs)
    assert "246802" not in repr(logs)
