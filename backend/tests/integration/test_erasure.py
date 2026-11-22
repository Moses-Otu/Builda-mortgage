"""Phase 4: retention and erasure (owner-run), and audited original downloads. SYNTHETIC data.

Erasing a case removes every row of it and its stored originals, keeps the append-only audit
skeleton (with its erasable details removed), leaves other cases untouched, and restores the
immutability triggers afterwards."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import asyncpg
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from meip.core.config import Settings
from meip.core.db import create_engine, create_sessionmaker
from meip.documents.storage import FilesystemBlobStore
from meip.retention.blobs import erase_blob
from meip.retention.erasure import CASE_TABLES, erase_case, expired_cases
from tests.conftest import TEST_DB, Firm, db_url
from tests.integration.test_profile_approval import Case, Worker, _case
from tests.synthetic_docs import payslip_series


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


async def _approved_case(firm: Firm, worker: Worker) -> tuple[Case, str]:
    c = await _case(firm)
    [slip] = payslip_series(months=1, faults=frozenset({"arithmetic_error"}), fault_at=0)
    doc = await c.upload(worker, slip.document.pdf())
    await c.review(doc)
    await c.declare(45_000)
    checks = (await c.firm.request("GET", f"{c.base}/checks")).json()
    for f in checks["findings"]:
        if f["status"] == "discrepancy":
            await c.firm.request(
                "POST", f"{c.base}/checks/{f['key']}/acknowledgement", json={"note": "Seen"}
            )
    await c.ready_for_approval()
    assert (await c.approve()).status_code == 201
    return c, doc["id"]


def test_every_case_table_is_erased() -> None:
    """Checked against the live schema in the next test; here the order is sane."""
    assert CASE_TABLES[-1] == "applicants"
    assert CASE_TABLES.index("financial_observations") < CASE_TABLES.index("field_reviews")
    assert CASE_TABLES.index("profile_approvals") < CASE_TABLES.index("applicants")


async def test_erasing_a_case_removes_everything_of_it_and_nothing_else(
    firm: Firm,
    worker: Worker,
    owner: asyncpg.Connection,
    owner_sessionmaker: async_sessionmaker[AsyncSession],
    blob_root: Path,
) -> None:
    # The table list covers every table with a case_id (audit_events stay by design).
    with_case_id = {
        r["table_name"]
        for r in await owner.fetch(
            "SELECT table_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND column_name = 'case_id'"
        )
    }
    assert with_case_id - {"audit_events"} == set(CASE_TABLES)

    gone, gone_doc = await _approved_case(firm, worker)
    kept, kept_doc = await _approved_case(firm, worker)
    resp = await gone.firm.request("GET", f"{gone.base}/documents/{gone_doc}/content")
    assert resp.status_code == 200  # an audited download, before erasure
    events_before = await owner.fetchval(
        "SELECT count(*) FROM audit_events WHERE case_id = $1", gone.id
    )
    details_before = await owner.fetchval(
        "SELECT count(*) FROM audit_event_details d JOIN audit_events e ON e.id = d.event_id "
        "WHERE e.case_id = $1",
        gone.id,
    )
    assert details_before > 0

    async with owner_sessionmaker() as session, session.begin():
        erased = await erase_case(
            session, firm.tenant_id, gone.id, operator="operator:pytest", reason="Test erasure"
        )
    store = FilesystemBlobStore(blob_root)
    for key in erased.blob_keys:
        assert await erase_blob(store, key)
        assert not (blob_root / key).exists()

    for table in (*CASE_TABLES, "cases"):
        column = "id" if table == "cases" else "case_id"
        query = f"SELECT count(*) FROM {table} WHERE {column} = $1"  # noqa: S608 - our list
        assert await owner.fetchval(query, gone.id) == 0, table
    # The audit skeleton remains (append-only), with its erasable details removed.
    events_after = await owner.fetchval(
        "SELECT count(*) FROM audit_events WHERE case_id = $1", gone.id
    )
    assert events_after == events_before + 1  # plus the erasure event
    remaining_details = await owner.fetch(
        "SELECT e.action FROM audit_event_details d JOIN audit_events e ON e.id = d.event_id "
        "WHERE e.case_id = $1",
        gone.id,
    )
    assert [r["action"] for r in remaining_details] == ["case.erased"]  # only its own reason
    assert erased.rows["profile_approvals"] == 1
    assert erased.rows["financial_observations"] > 0

    # The other case is untouched, and its downloads and pack still work.
    assert await owner.fetchval("SELECT count(*) FROM cases WHERE id = $1", kept.id) == 1
    resp = await kept.firm.request("GET", f"{kept.base}/documents/{kept_doc}/content")
    assert resp.status_code == 200
    # The immutability triggers are back on.
    with pytest.raises(asyncpg.IntegrityConstraintViolationError, match="immutable"):
        await owner.execute("DELETE FROM document_versions WHERE case_id = $1", kept.id)

    # The erased case is simply not found for the firm.
    assert (await gone.firm.request("GET", gone.base)).status_code == 404


async def test_original_downloads_are_audited(
    firm: Firm, worker: Worker, owner: asyncpg.Connection
) -> None:
    c = await _case(firm)
    doc = await c.upload(worker, payslip_series(months=1)[0].document.pdf())
    for _ in range(2):
        assert (
            await c.firm.request("GET", f"{c.base}/documents/{doc['id']}/content")
        ).status_code == 200
    count = await owner.fetchval(
        "SELECT count(*) FROM audit_events WHERE case_id = $1 AND action = 'document.downloaded'",
        c.id,
    )
    assert count == 2


async def test_the_retention_sweep_lists_only_cases_closed_long_enough(
    firm: Firm, owner: asyncpg.Connection, owner_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    old = await _case(firm)
    recent = await _case(firm)
    for c in (old, recent):
        resp = await c.move("closed", closure_reason="not_proceeding")
        assert resp.status_code == 200, resp.text
    await owner.execute(
        "UPDATE cases SET status_changed_at = now() - interval '400 days' WHERE id = $1", old.id
    )
    async with owner_sessionmaker() as session:
        due = await expired_cases(session, 365)
    ids = {case_id for _, case_id, _ in due}
    assert old.id in {str(i) for i in ids} or old.id in ids
    assert recent.id not in {str(i) for i in ids}
