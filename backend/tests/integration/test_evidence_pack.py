"""Stage 3-C: the evidence pack, built only from an approval. SYNTHETIC data only.

The pack is byte-identical for the same approval, before and after the case changes; every
relied-on original is checked against the hash in the snapshot, and one missing or altered
original refuses the whole export."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import asyncpg
import pypdfium2 as pdfium
import pytest

from meip.core.config import Settings
from meip.core.db import create_engine, create_sessionmaker
from meip.documents.storage import FilesystemBlobStore
from tests.conftest import TEST_DB, Firm, db_url
from tests.integration.test_profile_approval import Case, Worker, _case
from tests.synthetic_docs import p60, payslip_series


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


@pytest.fixture
async def case(firm: Firm, owner: asyncpg.Connection) -> Case:
    return await _case(firm)


def _pdf_text(data: bytes) -> str:
    pdf = pdfium.PdfDocument(data)
    return "\n".join(pdf[i].get_textpage().get_text_range() for i in range(len(pdf)))


async def _download(c: Case, approval_id: str) -> Any:
    return await c.firm.request("GET", f"{c.base}/profile/approvals/{approval_id}/pack")


async def _approved(c: Case, worker: Worker) -> tuple[dict[str, Any], bytes, dict[str, Any], str]:
    """A reviewed payslip with an acknowledged discrepancy, an unrecognised document, a
    declared figure; then approved. Returns the approval, the payslip's bytes and its doc."""
    [slip] = payslip_series(months=1, faults=frozenset({"arithmetic_error"}), fault_at=0)
    pdf = slip.document.pdf()
    doc = await c.upload(worker, pdf)
    await c.review(doc)
    unrecognised = await c.upload(worker, p60().pdf())
    assert unrecognised["status"] == "unrecognised"
    declared = await c.declare(45_000)
    checks = (await c.firm.request("GET", f"{c.base}/checks")).json()
    for f in checks["findings"]:
        if f["status"] == "discrepancy":
            resp = await c.firm.request(
                "POST",
                f"{c.base}/checks/{f['key']}/acknowledgement",
                json={"note": "Employer confirmed the arithmetic"},
            )
            assert resp.status_code == 201, resp.text
    await c.ready_for_approval()
    resp = await c.approve()
    assert resp.status_code == 201, resp.text
    return resp.json(), pdf, doc, declared


async def test_the_pack_is_complete_checked_and_byte_identical(
    case: Case, worker: Worker, owner: asyncpg.Connection
) -> None:
    approval, original, doc, declared = await _approved(case, worker)
    first = await _download(case, approval["id"])
    assert first.status_code == 200, first.text
    assert first.headers["content-type"] == "application/zip"
    assert "-v1.zip" in first.headers["content-disposition"]
    second = await _download(case, approval["id"])
    assert second.content == first.content  # byte-identical for the same approval

    z = zipfile.ZipFile(io.BytesIO(first.content))
    names = z.namelist()
    assert names[:3] == ["summary.pdf", "snapshot.json", "attestation.txt"]
    assert names[-1] == "manifest.json"
    originals = [n for n in names if n.startswith("originals/")]
    assert len(originals) == 1  # the reviewed payslip; the unrecognised document isn't relied on
    assert z.read(originals[0]) == original  # byte-identical original

    manifest = json.loads(z.read("manifest.json"))
    assert manifest["generator"] == "pack/1"
    assert manifest["approval"]["version"] == 1
    for f in manifest["files"]:
        assert hashlib.sha256(z.read(f["path"])).hexdigest() == f["sha256"]
    stored = await owner.fetchrow(
        "SELECT snapshot, snapshot_sha256, attestation_text FROM profile_approvals WHERE id = $1",
        approval["id"],
    )
    assert stored is not None
    assert z.read("snapshot.json") == stored["snapshot"].encode()
    assert hashlib.sha256(z.read("snapshot.json")).hexdigest() == stored["snapshot_sha256"]
    assert stored["attestation_text"] in z.read("attestation.txt").decode()

    text = _pdf_text(z.read("summary.pdf"))
    for needle in (
        "Evidence pack",
        "attestation/1",
        "Supported by",
        "Declared by the applicant",
        "Documentary evidence (broker-reviewed)",
        "Employer confirmed the arithmetic",
        "Not a lending decision",
        "page 1 of",
    ):
        assert needle in text, needle

    # Downloads are audited, with the pack's hash.
    events = await owner.fetch(
        "SELECT changes FROM audit_events WHERE case_id = $1 "
        "AND action = 'evidence_pack.downloaded'",
        case.id,
    )
    assert len(events) == 2
    assert {json.loads(e["changes"])["pack_sha256"] for e in events} == {
        hashlib.sha256(first.content).hexdigest()
    }

    # Reopen, change and approve again: version 1's pack is unchanged, version 2's differs.
    resp = await case.move("in_review", note="Client sent an update")
    assert resp.status_code == 200, resp.text
    await case.declare(45_100, supersedes=declared)  # within the 2 % threshold: no new discrepancy
    resp = await case.approve()
    assert resp.status_code == 201, resp.text
    second_approval = resp.json()
    again = await _download(case, approval["id"])
    assert again.content == first.content
    v2 = await _download(case, second_approval["id"])
    assert v2.status_code == 200
    assert v2.content != first.content
    del doc


async def test_a_missing_or_altered_original_refuses_the_whole_pack(
    case: Case, worker: Worker, owner: asyncpg.Connection, blob_root: Path
) -> None:
    approval, _, doc, _ = await _approved(case, worker)
    key = await owner.fetchval(
        "SELECT blob_key FROM document_versions WHERE document_id = $1", doc["id"]
    )
    path = blob_root / key
    good = path.read_bytes()

    path.write_bytes(good + b"%altered")
    resp = await _download(case, approval["id"])
    assert resp.status_code == 409
    assert resp.json()["type"].endswith("original-unavailable")
    assert resp.json()["document_id"] == doc["id"]
    assert resp.headers["content-type"].startswith("application/problem+json")

    path.unlink()
    resp = await _download(case, approval["id"])
    assert resp.status_code == 409
    assert resp.json()["type"].endswith("original-unavailable")

    # A refused export is not recorded as a download.
    assert (
        await owner.fetchval(
            "SELECT count(*) FROM audit_events WHERE case_id = $1 "
            "AND action = 'evidence_pack.downloaded'",
            case.id,
        )
        == 0
    )
    path.write_bytes(good)
    assert (await _download(case, approval["id"])).status_code == 200


async def test_a_tampered_snapshot_refuses_the_pack(
    case: Case, worker: Worker, owner: asyncpg.Connection
) -> None:
    approval, _, _, _ = await _approved(case, worker)
    await owner.execute(
        "UPDATE profile_approvals SET snapshot = snapshot || ' ' WHERE id = $1", approval["id"]
    )
    resp = await _download(case, approval["id"])
    assert resp.status_code == 409
    assert resp.json()["type"].endswith("snapshot-integrity")
