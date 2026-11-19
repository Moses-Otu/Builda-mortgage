"""Stage 2-B step 2B-1: bank statements uploaded, processed and extracted, end to end.

All statements are SYNTHETIC. These tests prove the pipeline against the generator's ground
truth; they make no claim about extraction accuracy on real bank statements."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import asyncpg
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from structlog.testing import capture_logs

from meip.api.deps import get_settings
from meip.core.config import Environment, Settings
from meip.core.db import create_engine, create_sessionmaker
from meip.documents.processing import run_once
from meip.documents.storage import FilesystemBlobStore
from tests.conftest import TEST_DB, Firm, db_url, synthetic_valid_pdf
from tests.synthetic_docs import (
    SeriesStatement,
    bank_statement_series,
    payslip_series,
    salaries_from,
)

Worker = tuple[async_sessionmaker[AsyncSession], FilesystemBlobStore, Settings]
# Sort code digits chosen not to collide with anything else in the test firm (its FRN is 123456).
ACCOUNT, SORT, SORT_PRINTED = "12344821", "607188", "60-71-88"


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
    def __init__(self, firm: Firm, case: dict[str, Any], applicant: dict[str, Any]) -> None:
        self.firm, self.case, self.applicant = firm, case, applicant
        self.base = f"/api/v1/cases/{case['id']}"

    async def upload(
        self, worker: Worker, pdf: bytes, document_type: str = "bank_statement"
    ) -> dict[str, Any]:
        resp = await self.firm.upload(
            self.case["id"], pdf, applicant_id=self.applicant["id"], document_type=document_type
        )
        assert resp.status_code == 201, resp.text
        while await run_once(*worker):
            pass
        doc: dict[str, Any] = (
            await self.firm.request("GET", f"{self.base}/documents/{resp.json()['id']}")
        ).json()
        return doc

    async def extraction(self, doc: dict[str, Any]) -> dict[str, Any]:
        resp = await self.firm.request("GET", f"{self.base}/documents/{doc['id']}/extraction")
        assert resp.status_code == 200, resp.text
        out: dict[str, Any] = resp.json()
        return out


@pytest.fixture
async def case(firm: Firm, owner: asyncpg.Connection) -> Case:
    c = await firm.create_case()
    return Case(firm, c, await firm.add_applicant(c["id"]))


def series(**kw: Any) -> list[SeriesStatement]:
    return bank_statement_series(
        salaries_from(payslip_series(months=4)), **{"sort_code": SORT_PRINTED, **kw}
    )


async def _fingerprint(owner: asyncpg.Connection, doc: dict[str, Any]) -> str | None:
    value: str | None = await owner.fetchval(
        "SELECT r.account_fingerprint FROM extraction_runs r "
        "JOIN document_versions v ON v.id = r.document_version_id WHERE v.document_id = $1",
        doc["id"],
    )
    return value


# --- extraction -----------------------------------------------------------------


async def test_a_statement_is_extracted_with_provenance(case: Case, worker: Worker) -> None:
    s = series(layout="two_pages")[0]
    doc = await case.upload(worker, s.document.pdf())
    assert doc["status"] == "extracted", doc
    assert doc["document_type"] == "bank_statement"
    assert doc["page_count"] == 2
    x = await case.extraction(doc)
    assert x["classification"] == "bank_statement"
    assert x["schema_version"] == "bank_statement/1.0.0"
    assert x["classifier_version"] == "statement-rules/1"
    assert x["missing_required"] == []
    summary = {f["field_code"]: f for f in x["summary"]}
    assert summary["account_ending"]["value_text"] == "4821"
    assert summary["account_ending"]["raw_text"] == "****4821"
    assert summary["opening_balance"]["value_money_minor"] == s.opening_minor
    assert summary["closing_balance"]["value_money_minor"] == s.closing_minor
    rows = x["transactions"]
    assert [r["kind"] for r in rows] == [r.kind for r in s.rows]
    assert [r["description"].split("\n") for r in rows] == [r.description for r in s.rows]
    grocer = next(r for r in rows if r["description"].startswith("CARD PAYMENT GROCER"))
    assert grocer["description"] == "CARD PAYMENT GROCER\nLONDON GB"
    assert [c["text"] for c in grocer["cells"]["description"]] == [
        "CARD PAYMENT GROCER",
        "LONDON GB",
    ]
    assert grocer["cells"]["paid_out"]["text"] == "54.20"
    assert grocer["paid_out_minor"] == 5_420
    assert grocer["paid_in_minor"] is None
    assert {r["row_evidence"]["page_number"] for r in rows} == {1, 2}


async def test_no_structured_column_holds_the_account_number_or_sort_code(
    case: Case, worker: Worker, owner: asyncpg.Connection
) -> None:
    await case.upload(worker, series()[0].document.pdf())
    tenant = case.firm.tenant_id
    for table, exclude in (
        ("extracted_fields", ""),
        ("extracted_transactions", ""),
        ("extraction_runs", " - 'raw_output'"),
        ("documents", ""),
        ("audit_events", ""),
        ("audit_event_details", ""),
    ):
        rows = await owner.fetch(
            f"SELECT (to_jsonb(t){exclude})::text AS row FROM {table} t "  # noqa: S608 # fixed
            "WHERE tenant_id = $1",
            tenant,
        )
        text = " ".join(r["row"] for r in rows)
        assert ACCOUNT not in text, table
        assert SORT_PRINTED not in text, table
        assert SORT not in text, table
    # Stated in the plan (C1): the raw provider output is the whole document's text, so it
    # does hold them. It is classed and erased with the original.
    raw = await owner.fetchval(
        "SELECT raw_output::text FROM extraction_runs WHERE tenant_id = $1", tenant
    )
    assert ACCOUNT in raw


async def test_fingerprints_group_the_same_account_only(
    case: Case, worker: Worker, owner: asyncpg.Connection
) -> None:
    first, second = series()[:2]
    other = series(sort_code="40-11-22")[2]  # same last four digits, another account
    masked = series(masked_number=True)[3]
    prints = [
        await _fingerprint(owner, await case.upload(worker, s.document.pdf()))
        for s in (first, second, other, masked)
    ]
    assert prints[0] is not None
    assert len(prints[0]) == 64
    assert prints[0] == prints[1]
    assert prints[2] != prints[0]
    assert prints[3] is None


@pytest.mark.parametrize(
    ("fault", "code"),
    [
        ("multiple_accounts", "statement_multiple_accounts"),
        ("foreign_currency", "statement_foreign_currency"),
    ],
)
async def test_refusals_fail_with_the_reason_and_store_no_rows(
    case: Case, worker: Worker, owner: asyncpg.Connection, fault: str, code: str
) -> None:
    doc = await case.upload(worker, series(positions={fault: 0})[0].document.pdf())
    assert doc["status"] == "failed"
    assert doc["failure_code"] == code
    x = await case.extraction(doc)
    assert f"refused:{code}" in x["markers"]
    assert x["summary"] == []
    assert x["transactions"] == []
    assert await _fingerprint(owner, doc) is None


async def test_unreadable_rows_are_kept(case: Case, worker: Worker) -> None:
    doc = await case.upload(worker, series(positions={"unparsed_row": 0})[0].document.pdf())
    rows = (await case.extraction(doc))["transactions"]
    [bad] = [r for r in rows if r["kind"] == "unparsed"]
    assert bad["description"] == "DIRECT DEBIT ENERGY CO"
    assert bad["paid_out_minor"] is None
    assert "O" in bad["cells"]["paid_out"]["text"]


async def test_a_payslip_uploaded_as_a_statement_is_unrecognised(
    case: Case, worker: Worker
) -> None:
    doc = await case.upload(worker, payslip_series(months=1)[0].document.pdf())
    assert doc["status"] == "unrecognised"
    x = await case.extraction(doc)
    assert x["classification"] == "unrecognised"
    assert x["transactions"] == []


async def test_a_statement_uploaded_as_a_payslip_is_unrecognised(
    case: Case, worker: Worker
) -> None:
    doc = await case.upload(worker, series()[0].document.pdf(), document_type="payslip")
    assert doc["status"] == "unrecognised"


async def test_statements_may_have_more_pages_than_payslips(case: Case, worker: Worker) -> None:
    payslip = await case.upload(worker, synthetic_valid_pdf(15, "p"), document_type="payslip")
    statement = await case.upload(worker, synthetic_valid_pdf(15, "s"))
    too_long = await case.upload(worker, synthetic_valid_pdf(21, "l"))
    assert payslip["failure_code"] == "too_many_pages"
    assert statement["failure_code"] != "too_many_pages"
    assert statement["page_count"] == 15
    assert too_long["failure_code"] == "too_many_pages"


async def test_statements_cannot_be_reviewed_as_payslips(case: Case, worker: Worker) -> None:
    doc = await case.upload(worker, series()[0].document.pdf())
    resp = await case.firm.request(
        "POST",
        f"{case.base}/documents/{doc['id']}/review",
        if_match=doc["version"],
        json={"income_source_id": doc["id"], "decisions": [{"ordinal": 1, "decision": "accept"}]},
    )
    assert resp.status_code == 409


async def test_extraction_audit_and_logs_hold_no_statement_content(
    case: Case, worker: Worker
) -> None:
    with capture_logs() as logs:
        doc = await case.upload(worker, series()[0].document.pdf())
    events = (await case.firm.request("GET", f"{case.base}/events")).json()["items"]
    [event] = [e for e in events if e["action"] == "document.extracted"]
    assert event["changes"]["transaction_count"] == 7
    assert event["changes"]["schema_version"] == "bank_statement/1.0.0"
    for secret in (ACCOUNT, "4821", "SERIES EMPLOYER", "LANDLORD", "2,805.84", "280584"):
        assert secret not in repr(event), secret
        assert secret not in repr(logs), secret
    assert doc["status"] == "extracted"


# --- the fail-closed gate (plan, section 2.3) ------------------------------------


@pytest.fixture
def deployed(app: FastAPI, settings: Settings) -> Iterator[None]:
    """The API as if deployed: settings say production (validation bypassed for the test)."""
    production = settings.model_copy(update={"env": Environment.PRODUCTION})
    app.dependency_overrides[get_settings] = lambda: production
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_settings, None)


async def test_statements_are_refused_outside_local_and_test(case: Case, deployed: None) -> None:
    resp = await case.firm.upload(
        case.case["id"], series()[0].document.pdf(), document_type="bank_statement"
    )
    assert resp.status_code == 404
    assert resp.json()["type"] == "urn:meip:problem:feature-disabled"
    payslip = await case.firm.upload(case.case["id"], payslip_series(months=1)[0].document.pdf())
    assert payslip.status_code == 201  # payslips are unaffected


def test_the_settings_gate() -> None:
    allowed = {
        env: Settings.model_construct(env=env).bank_statements_allowed for env in Environment
    }
    assert allowed == {
        Environment.LOCAL: True,
        Environment.TEST: True,
        Environment.STAGING: False,
        Environment.PRODUCTION: False,
    }
