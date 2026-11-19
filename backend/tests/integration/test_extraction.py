"""Step 2-C: classification and extraction through the real pipeline (upload -> ingest job ->
extract job -> API). All documents are SYNTHETIC.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import asyncpg
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from structlog.testing import capture_logs

from meip.core.config import Settings
from meip.core.db import create_engine, create_sessionmaker
from meip.documents.processing import MAX_ATTEMPTS, run_once
from meip.documents.storage import FilesystemBlobStore
from tests.conftest import TEST_DB, Firm, db_url, synthetic_valid_pdf
from tests.synthetic_docs import NON_PAYSLIPS, PAYSLIPS, SyntheticDocument
from tests.unit.test_extraction_unit import visible_text

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
        await conn.execute(  # park other tests' leftover jobs
            "UPDATE jobs SET status = 'done', locked_until = NULL "
            "WHERE status IN ('queued', 'running')"
        )
        yield conn
    finally:
        await conn.close()


@pytest.fixture
async def case(firm: Firm, owner: asyncpg.Connection) -> dict[str, Any]:
    created: dict[str, Any] = await firm.create_case()
    return created


async def _process(firm: Firm, case: dict[str, Any], pdf: bytes, worker: Worker) -> dict[str, Any]:
    resp = await firm.upload(case["id"], pdf)
    assert resp.status_code == 201, resp.text
    while await run_once(*worker):
        pass
    doc: dict[str, Any] = (
        await firm.request("GET", f"/api/v1/cases/{case['id']}/documents/{resp.json()['id']}")
    ).json()
    return doc


def _extraction_url(case: dict[str, Any], doc: dict[str, Any]) -> str:
    return f"/api/v1/cases/{case['id']}/documents/{doc['id']}/extraction"


async def _events(firm: Firm, case: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = (
        await firm.request("GET", f"/api/v1/cases/{case['id']}/events")
    ).json()["items"]
    return items


# --- outcome 1: extracted --------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(PAYSLIPS))
async def test_payslip_is_extracted_with_evidence_that_matches_the_original(
    firm: Firm, case: dict[str, Any], worker: Worker, name: str
) -> None:
    synthetic, expected = PAYSLIPS[name]()
    doc = await _process(firm, case, synthetic.pdf(), worker)
    assert doc["status"] == "extracted"

    out = (await firm.request("GET", _extraction_url(case, doc))).json()
    assert out["provider"] == "synthetic-fixture"  # always identifiable as synthetic
    assert (out["classification"], out["schema_version"]) == ("payslip", "payslip/1.0.0")
    assert out["missing_required"] == []
    assert {f["field_code"]: f["raw_text"] for f in out["summary"]} == expected.summary
    assert [(f["label_text"], f["raw_text"], f["field_code"]) for f in out["payments"]] == (
        expected.payments
    )
    assert [(f["label_text"], f["raw_text"], f["field_code"]) for f in out["deductions"]] == (
        expected.deductions
    )
    assert {f["field_code"]: f["raw_text"] for f in out["year_to_date"]} == expected.year_to_date

    # End to end: the stored original really shows each value and label at its evidence.
    original = (
        await firm.request("GET", f"/api/v1/cases/{case['id']}/documents/{doc['id']}/content")
    ).content
    fields = out["summary"] + out["payments"] + out["deductions"] + out["year_to_date"]
    for f in fields:
        v, lbl = f["value_evidence"], f["label_evidence"]
        assert (
            visible_text(original, v["page_number"], (v["x0"], v["y0"], v["x1"], v["y1"]))
            == (f["raw_text"])
        )
        assert (
            visible_text(original, lbl["page_number"], (lbl["x0"], lbl["y0"], lbl["x1"], lbl["y1"]))
            == f["label_text"]
        )
        assert f["unit"] == "pt"
        assert 0 <= v["x0"] <= v["x1"] <= f["page_width"]

    events = await _events(firm, case)
    extracted = [e for e in events if e["action"] == "document.extracted"]
    assert len(extracted) == 1
    assert extracted[0]["actor_ref"] == "worker"
    assert extracted[0]["changes"]["field_count"] == len(fields)


async def test_money_is_normalised_to_pence(
    firm: Firm, case: dict[str, Any], worker: Worker
) -> None:
    synthetic, _ = PAYSLIPS["classic"]()
    doc = await _process(firm, case, synthetic.pdf(), worker)
    out = (await firm.request("GET", _extraction_url(case, doc))).json()
    net = next(f for f in out["summary"] if f["field_code"] == "net_pay")
    assert (net["value_money_minor"], net["currency"]) == (245196, "GBP")
    assert net["confidence"] is None  # the synthetic provider records none by default


# --- outcome 2: unrecognised ---------------------------------------------------------------------


@pytest.mark.parametrize("name", ["p60", "bank-statement", "offer-letter"])
async def test_look_alike_is_unrecognised_with_a_run_and_no_fields(
    firm: Firm, case: dict[str, Any], worker: Worker, owner: asyncpg.Connection, name: str
) -> None:
    doc = await _process(firm, case, NON_PAYSLIPS[name]().pdf(), worker)
    assert (doc["status"], doc["failure_code"]) == ("unrecognised", None)
    out = (await firm.request("GET", _extraction_url(case, doc))).json()
    assert out["classification"] == "unrecognised"
    assert out["summary"] == out["payments"] == out["deductions"] == []
    assert out["missing_required"] == []
    events = await _events(firm, case)
    assert [e["action"] for e in events if e["action"].startswith("document.")][-1] == (
        "document.unrecognised"
    )


# --- outcome 3: provider failure ------------------------------------------------------------------


async def test_provider_failure_fails_the_document_without_a_run(
    firm: Firm, case: dict[str, Any], worker: Worker, owner: asyncpg.Connection
) -> None:
    doc = await _process(firm, case, synthetic_valid_pdf(), worker)  # valid PDF, no layout
    assert (doc["status"], doc["failure_code"]) == ("failed", "extraction_failed")
    assert (await firm.request("GET", _extraction_url(case, doc))).status_code == 404
    runs = await owner.fetchval(
        "SELECT count(*) FROM extraction_runs WHERE document_version_id = $1",
        uuid.UUID(doc["original"]["id"]),
    )
    assert runs == 0


async def test_transient_errors_during_extraction_retry_then_fail(
    firm: Firm,
    case: dict[str, Any],
    worker: Worker,
    owner: asyncpg.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessionmaker, store, settings = worker
    synthetic, _ = PAYSLIPS["classic"]()
    resp = await firm.upload(case["id"], synthetic.pdf())
    assert await run_once(*worker)  # ingest succeeds and queues extraction

    async def unavailable(*args: Any, **kwargs: Any) -> None:
        raise OSError("storage unavailable")

    monkeypatch.setattr(store, "read_into", unavailable)
    for _ in range(MAX_ATTEMPTS):
        assert await run_once(sessionmaker, store, settings)
        await owner.execute("UPDATE jobs SET run_after = now() WHERE status = 'queued'")
    doc = (
        await firm.request("GET", f"/api/v1/cases/{case['id']}/documents/{resp.json()['id']}")
    ).json()
    assert (doc["status"], doc["failure_code"]) == ("failed", "extraction_failed")


async def test_extraction_runs_once_per_version(
    firm: Firm, case: dict[str, Any], worker: Worker, owner: asyncpg.Connection
) -> None:
    synthetic, _ = PAYSLIPS["classic"]()
    doc = await _process(firm, case, synthetic.pdf(), worker)
    version = uuid.UUID(doc["original"]["id"])
    await owner.execute(  # a duplicate delivery of the same extract job
        "UPDATE jobs SET status = 'queued', run_after = now() "
        "WHERE document_version_id = $1 AND kind = 'document.extract'",
        version,
    )
    assert await run_once(*worker)
    assert (
        await owner.fetchval(
            "SELECT count(*) FROM extraction_runs WHERE document_version_id = $1", version
        )
        == 1
    )


async def test_extraction_is_404_before_it_runs(firm: Firm, case: dict[str, Any]) -> None:
    resp = await firm.upload(case["id"], PAYSLIPS["classic"]()[0].pdf())
    url = f"/api/v1/cases/{case['id']}/documents/{resp.json()['id']}/extraction"
    assert (await firm.request("GET", url)).status_code == 404


# --- no extracted data or raw output in logs or audit ------------------------------------------


async def test_no_document_content_reaches_logs_or_audit(
    firm: Firm, case: dict[str, Any], worker: Worker
) -> None:
    distinctive = SyntheticDocument()
    rows = [
        ("Pay Date", "13/03/2026"),
        ("Tax Code", "ZQ987X"),
        ("Payments", ""),
        ("Zebracorn Allowance", "£7,777.77"),
        ("Total Gross Pay", "£8,642.19"),
        ("Deductions", ""),
        ("Quokka Levy", "£1,357.91"),
        ("Total Deductions", "£2,468.02"),
        ("Net Pay", "£6,174.17"),
    ]
    for i, (label, value) in enumerate(rows):
        distinctive.text(1, 50, 100 + 15 * i, label)
        if value:
            distinctive.right(1, 520, 100 + 15 * i, value)
    secrets = [
        "Zebracorn",
        "Quokka",
        "7,777.77",
        "777777",
        "8,642.19",
        "1,357.91",
        "6,174.17",
        "ZQ987X",
        "13/03/2026",
        "SYNTHETIC-LAYOUT",
    ]
    with capture_logs() as logs:
        payslip = await _process(firm, case, distinctive.pdf(), worker)
        await firm.request("GET", _extraction_url(case, payslip))
        await _process(firm, case, NON_PAYSLIPS["p60"]().pdf(), worker)  # unrecognised
        await _process(firm, case, synthetic_valid_pdf(), worker)  # provider failure
    assert payslip["status"] == "extracted"
    events = await _events(firm, case)
    worker_events = [e for e in events if e["actor_type"] == "system"]
    assert {e["action"] for e in worker_events} >= {
        "document.extracted",
        "document.unrecognised",
        "document.processing_failed",
    }
    haystack = repr(logs) + repr([(e["changes"], e["details"]) for e in worker_events])
    leaked = [s for s in secrets if s in haystack]
    assert leaked == []


# --- database rules ------------------------------------------------------------------------------


async def test_runs_and_fields_are_immutable_and_tenant_bound(
    firm: Firm, case: dict[str, Any], worker: Worker, make_firm: Any
) -> None:
    doc = await _process(firm, case, PAYSLIPS["classic"]()[0].pdf(), worker)
    assert doc["status"] == "extracted"
    conn = await asyncpg.connect(db_url("meip_app", TEST_DB, driver="postgresql"))
    try:
        await conn.execute("SELECT set_config('app.tenant_id', $1, false)", str(firm.tenant_id))
        for statement in (
            "UPDATE extraction_runs SET classification = 'unrecognised'",
            "DELETE FROM extraction_runs",
            "UPDATE extracted_fields SET raw_text = 'x'",
            "DELETE FROM extracted_fields",
        ):
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.execute(statement)

        run_id = await conn.fetchval(
            "SELECT id FROM extraction_runs WHERE document_version_id = $1",
            uuid.UUID(doc["original"]["id"]),
        )
        insert = (
            "INSERT INTO extracted_fields (id, tenant_id, case_id, run_id, section, ordinal,"
            " label_text, raw_text, page_number, page_width, page_height, unit, x0, y0, x1, y1,"
            " label_x0, label_y0, label_x1, label_y1) VALUES ($1, $2, $3, $4, 'summary', 99,"
            " 'L', 'V', 1, 595, 842, 'pt', $5, 10, 60, 20, 0, 10, 40, 20)"
        )
        with pytest.raises(asyncpg.CheckViolationError):  # box outside the page
            await conn.execute(
                insert, uuid.uuid4(), firm.tenant_id, uuid.UUID(case["id"]), run_id, -5.0
            )
        other = await make_firm()
        other_case = await other.create_case()
        await conn.execute("SELECT set_config('app.tenant_id', $1, false)", str(other.tenant_id))
        with pytest.raises(asyncpg.ForeignKeyViolationError):  # another firm's run
            await conn.execute(
                insert, uuid.uuid4(), other.tenant_id, uuid.UUID(other_case["id"]), run_id, 5.0
            )
    finally:
        await conn.close()


# --- final review: duplicates, preserved evidence, isolation with real data ------------------


async def test_concurrent_extraction_attempts_produce_one_run(
    firm: Firm, case: dict[str, Any], worker: Worker, owner: asyncpg.Connection
) -> None:
    """Two attempts at the same version at once (a retry racing a stale worker): the document
    row lock serialises them, the second sees the result and skips, and one run exists."""
    from meip.documents.processing import WORKER_ACTOR, extract_document
    from meip.tenancy.context import OperationContext, open_tenant_scope

    sessionmaker, store, settings = worker
    resp = await firm.upload(case["id"], PAYSLIPS["classic"]()[0].pdf())
    assert await run_once(*worker)  # ingest only: the document is now ready
    version = uuid.UUID(resp.json()["original"]["id"])

    async def attempt() -> str:
        async with open_tenant_scope(sessionmaker, firm.tenant_id) as scope:
            ctx = OperationContext(scope=scope, actor=WORKER_ACTOR)
            outcome = await extract_document(ctx, store, version, settings)
            await ctx.commit()
            return outcome

    outcomes = sorted(await asyncio.gather(attempt(), attempt()))
    assert outcomes == ["extracted", "skipped"]
    assert (
        await owner.fetchval(
            "SELECT count(*) FROM extraction_runs WHERE document_version_id = $1", version
        )
        == 1
    )


async def test_the_database_refuses_a_second_run_for_a_version(
    firm: Firm, case: dict[str, Any], worker: Worker
) -> None:
    doc = await _process(firm, case, PAYSLIPS["classic"]()[0].pdf(), worker)
    conn = await asyncpg.connect(db_url("meip_app", TEST_DB, driver="postgresql"))
    try:
        await conn.execute("SELECT set_config('app.tenant_id', $1, false)", str(firm.tenant_id))
        with pytest.raises(asyncpg.UniqueViolationError):
            await conn.execute(
                "INSERT INTO extraction_runs (id, tenant_id, case_id, document_version_id,"
                " provider, model_version, schema_version, classifier_version, classification,"
                " markers, raw_output, raw_output_sha256, created_at) VALUES ($1, $2, $3, $4,"
                " 'x', 'x', 'x', 'x', 'payslip', '[]', '{}', repeat('0', 64), now())",
                uuid.uuid4(),
                firm.tenant_id,
                uuid.UUID(case["id"]),
                uuid.UUID(doc["original"]["id"]),
            )
    finally:
        await conn.close()


async def test_failed_extraction_preserves_the_original(
    firm: Firm, case: dict[str, Any], worker: Worker
) -> None:
    pdf = synthetic_valid_pdf()
    doc = await _process(firm, case, pdf, worker)
    assert (doc["status"], doc["failure_code"]) == ("failed", "extraction_failed")
    assert doc["original"]["sha256"] == hashlib.sha256(pdf).hexdigest()
    download = await firm.request(
        "GET", f"/api/v1/cases/{case['id']}/documents/{doc['id']}/content"
    )
    assert (download.status_code, download.content) == (200, pdf)


async def test_unrecognised_documents_keep_their_classification_evidence(
    firm: Firm, case: dict[str, Any], worker: Worker, owner: asyncpg.Connection
) -> None:
    doc = await _process(firm, case, NON_PAYSLIPS["p60"]().pdf(), worker)
    out = (await firm.request("GET", _extraction_url(case, doc))).json()
    assert out["classification"] == "unrecognised"
    assert out["markers"] == ["tax_code", "paye", "national_insurance", "exclusion:p60"]
    assert (out["provider"], out["classifier_version"]) == ("synthetic-fixture", "payslip-rules/1")
    row = await owner.fetchrow(
        "SELECT raw_output ->> 'source' AS source, raw_output_sha256 FROM extraction_runs"
        " WHERE document_version_id = $1",
        uuid.UUID(doc["original"]["id"]),
    )
    assert row is not None
    assert row["source"] == "synthetic-fixture"  # the raw provider output is retained
    assert len(row["raw_output_sha256"]) == 64


async def _counts(conn: asyncpg.Connection, query: str) -> tuple[int, int]:
    row = await conn.fetchrow(query)
    assert row is not None
    return int(row[0]), int(row[1])


async def test_extraction_results_are_isolated_between_firms(
    firm: Firm, case: dict[str, Any], worker: Worker, make_firm: Any
) -> None:
    """With real extraction rows present (the generic RLS tests see these tables empty)."""
    doc = await _process(firm, case, PAYSLIPS["classic"]()[0].pdf(), worker)
    assert doc["status"] == "extracted"
    other = await make_firm()

    assert (await other.request("GET", _extraction_url(case, doc))).status_code == 404

    conn = await asyncpg.connect(db_url("meip_app", TEST_DB, driver="postgresql"))
    try:
        counts = (
            "SELECT (SELECT count(*) FROM extraction_runs), (SELECT count(*) FROM extracted_fields)"
        )
        await conn.execute("SELECT set_config('app.tenant_id', $1, false)", str(other.tenant_id))
        assert await _counts(conn, counts) == (0, 0)
        await conn.execute("SELECT set_config('app.tenant_id', $1, false)", str(firm.tenant_id))
        runs, fields = await _counts(conn, counts)
        assert runs >= 1
        assert fields > 0
        await conn.execute("SELECT set_config('app.tenant_id', '', false)")
        assert await _counts(conn, counts) == (0, 0)  # no tenant: nothing
    finally:
        await conn.close()
