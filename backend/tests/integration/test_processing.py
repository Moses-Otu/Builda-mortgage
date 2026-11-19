"""Step 2-B: background processing - queue, claiming, fencing, retries and the ingest job.

All documents are SYNTHETIC (``synthetic_valid_pdf`` / ``synthetic_pdf``).
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import asyncpg
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from meip.audit import service as audit_service
from meip.core.config import Settings
from meip.core.db import create_engine, create_sessionmaker
from meip.core.errors import ForbiddenError
from meip.documents import processing
from meip.documents.models import JobStatus
from meip.documents.processing import (
    MAX_ATTEMPTS,
    WORKER_ACTOR,
    claim,
    enqueue_ingest,
    ingest,
    run_once,
    run_worker,
)
from meip.documents.storage import FilesystemBlobStore
from meip.tenancy.context import Actor, OperationContext, open_tenant_scope
from meip.tenancy.models import Role
from meip.tenancy.permissions import ROLE_PERMISSIONS, Permission
from tests.conftest import TEST_DB, Firm, db_url, synthetic_pdf, synthetic_valid_pdf

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
        # Other tests' uploads leave queued jobs behind: park them so each test here sees
        # only its own work.
        await conn.execute(
            "UPDATE jobs SET status = 'done', locked_until = NULL "
            "WHERE status IN ('queued', 'running')"
        )
        yield conn
    finally:
        await conn.close()


@pytest.fixture
async def case(firm: Firm) -> dict[str, Any]:
    created = await firm.create_case()
    created["applicant"] = await firm.add_applicant(created["id"])
    return created


async def _upload(firm: Firm, case: dict[str, Any], content: bytes) -> dict[str, Any]:
    resp = await firm.upload(case["id"], content)
    assert resp.status_code == 201, resp.text
    out: dict[str, Any] = resp.json()
    return out


async def _document(firm: Firm, case: dict[str, Any], document_id: str) -> dict[str, Any]:
    out: dict[str, Any] = (
        await firm.request("GET", f"/api/v1/cases/{case['id']}/documents/{document_id}")
    ).json()
    return out


async def _job(
    owner: asyncpg.Connection, version_id: str, kind: str = "document.ingest"
) -> asyncpg.Record:
    # Each version can have several jobs (ingest, then extract): always ask for one kind.
    row = await owner.fetchrow(
        "SELECT * FROM jobs WHERE document_version_id = $1 AND kind = $2",
        uuid.UUID(version_id),
        kind,
    )
    assert row is not None
    return row


async def _drain(worker: Worker) -> int:
    processed = 0
    while await run_once(*worker):
        processed += 1
    return processed


# --- enqueue -----------------------------------------------------------------------------


async def test_upload_enqueues_one_job_in_the_same_transaction(
    firm: Firm, case: dict[str, Any], owner: asyncpg.Connection
) -> None:
    doc = await _upload(firm, case, synthetic_valid_pdf())
    job = await _job(owner, doc["original"]["id"])
    assert (job["kind"], job["status"], job["attempts"]) == ("document.ingest", "queued", 0)
    assert job["tenant_id"] == firm.tenant_id

    # Enqueueing again (e.g. a retried request) is a no-op.
    sessionmaker = create_sessionmaker(create_engine(db_url("meip_app", TEST_DB), pool_size=1))
    async with open_tenant_scope(sessionmaker, firm.tenant_id) as scope:
        await enqueue_ingest(
            scope.session, firm.tenant_id, uuid.UUID(case["id"]), uuid.UUID(doc["original"]["id"])
        )
        await scope.session.commit()
    count = await owner.fetchval(
        "SELECT count(*) FROM jobs WHERE document_version_id = $1",
        uuid.UUID(doc["original"]["id"]),
    )
    assert count == 1


async def test_failed_upload_leaves_no_job(
    firm: Firm, case: dict[str, Any], owner: asyncpg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def failing_record(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("audit store unavailable")

    before = await owner.fetchval("SELECT count(*) FROM jobs")
    monkeypatch.setattr(audit_service, "record", failing_record)
    resp = await firm.upload(case["id"], synthetic_valid_pdf())
    monkeypatch.undo()
    assert resp.status_code == 500
    assert await owner.fetchval("SELECT count(*) FROM jobs") == before
    assert (await firm.request("GET", f"/api/v1/cases/{case['id']}/documents")).json() == []


async def test_a_job_cannot_point_at_another_tenants_document(
    firm: Firm, case: dict[str, Any], make_firm: Any, owner: asyncpg.Connection
) -> None:
    other = await make_firm()
    other_case = await other.create_case()
    foreign = await _upload(other, other_case, synthetic_valid_pdf())
    # Remove the foreign document's own job so the composite foreign key, not the
    # uniqueness key, is what refuses the forged job.
    await owner.execute(
        "DELETE FROM jobs WHERE document_version_id = $1", uuid.UUID(foreign["original"]["id"])
    )
    conn = await asyncpg.connect(db_url("meip_app", TEST_DB, driver="postgresql"))
    try:
        await conn.execute("SELECT set_config('app.tenant_id', $1, false)", str(firm.tenant_id))
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await conn.execute(
                "INSERT INTO jobs (id, tenant_id, case_id, kind, document_version_id, status,"
                " attempts, run_after, created_at, updated_at) VALUES ($1, $2, $3,"
                " 'document.ingest', $4, 'queued', 0, now(), now(), now())",
                uuid.uuid4(),
                firm.tenant_id,
                uuid.UUID(case["id"]),
                uuid.UUID(foreign["original"]["id"]),
            )
    finally:
        await conn.close()


# --- ingest outcomes ---------------------------------------------------------------------


async def test_valid_pdf_becomes_ready_and_is_audited(
    firm: Firm, case: dict[str, Any], owner: asyncpg.Connection, worker: Worker
) -> None:
    doc = await _upload(firm, case, synthetic_valid_pdf(pages=3))
    assert (await _document(firm, case, doc["id"]))["status"] == "received"
    assert await run_once(*worker)  # the ingest job only
    extract_jobs = await owner.fetchval(
        "SELECT count(*) FROM jobs WHERE document_version_id = $1 AND kind = 'document.extract'"
        " AND status = 'queued'",
        uuid.UUID(doc["original"]["id"]),
    )
    assert extract_jobs == 1  # queued in the same transaction as the ingest outcome

    out = await _document(firm, case, doc["id"])
    assert (out["status"], out["page_count"], out["failure_code"]) == ("ready", 3, None)
    job = await _job(owner, doc["original"]["id"])
    assert (job["status"], job["attempts"], job["locked_until"]) == ("done", 1, None)

    events = (await firm.request("GET", f"/api/v1/cases/{case['id']}/events")).json()["items"]
    processed = [e for e in events if e["action"] == "document.processed"]
    assert len(processed) == 1
    assert (processed[0]["actor_type"], processed[0]["actor_ref"]) == ("system", "worker")
    assert processed[0]["correlation_id"] == str(job["id"])
    assert processed[0]["changes"]["status"] == {"from": "received", "to": "ready"}
    assert processed[0]["changes"]["page_count"] == 3

    # Running the job again changes nothing.
    async with open_tenant_scope(worker[0], firm.tenant_id) as scope:
        ctx = OperationContext(scope=scope, actor=WORKER_ACTOR)
        assert (
            await ingest(ctx, worker[1], uuid.UUID(doc["original"]["id"]), worker[2]) == "skipped"
        )


@pytest.mark.parametrize(
    ("content", "code"),
    [
        (lambda: synthetic_valid_pdf(encrypted=True), "encrypted_pdf"),
        (lambda: synthetic_pdf(), "unreadable_pdf"),
        (lambda: synthetic_valid_pdf(pages=11), "too_many_pages"),
    ],
    ids=["encrypted", "unreadable", "too-many-pages"],
)
async def test_bad_pdfs_fail_with_a_code_and_are_not_retried(
    firm: Firm,
    case: dict[str, Any],
    owner: asyncpg.Connection,
    worker: Worker,
    content: Any,
    code: str,
) -> None:
    doc = await _upload(firm, case, content())
    await _drain(worker)
    out = await _document(firm, case, doc["id"])
    assert (out["status"], out["failure_code"], out["page_count"]) == ("failed", code, None)
    job = await _job(owner, doc["original"]["id"])
    assert (job["status"], job["attempts"]) == ("done", 1)  # finished, not retried


async def test_tampered_and_missing_originals_fail(
    firm: Firm, case: dict[str, Any], owner: asyncpg.Connection, worker: Worker, blob_root: Path
) -> None:
    tampered = await _upload(firm, case, synthetic_valid_pdf())
    missing = await _upload(firm, case, synthetic_valid_pdf())
    for doc, change in ((tampered, "append"), (missing, "delete")):
        key = await owner.fetchval(
            "SELECT blob_key FROM document_versions WHERE id = $1",
            uuid.UUID(doc["original"]["id"]),
        )
        path = blob_root / str(key)
        if change == "append":
            path.write_bytes(path.read_bytes() + b"tampered")
        else:
            path.unlink()
    await _drain(worker)
    assert (await _document(firm, case, tampered["id"]))["failure_code"] == "hash_mismatch"
    assert (await _document(firm, case, missing["id"]))["failure_code"] == "missing_original"


async def test_a_pdf_check_that_runs_too_long_fails_as_timeout(
    firm: Firm, case: dict[str, Any], worker: Worker
) -> None:
    doc = await _upload(firm, case, synthetic_valid_pdf())
    sessionmaker, store, settings = worker
    impatient = settings.model_copy(update={"pdf_check_timeout_seconds": 0.001})
    await _drain((sessionmaker, store, impatient))
    assert (await _document(firm, case, doc["id"]))["failure_code"] == "timeout"


# --- claiming, fencing and retries -------------------------------------------------------


async def test_concurrent_workers_claim_each_job_exactly_once(
    firm: Firm, case: dict[str, Any], owner: asyncpg.Connection, worker: Worker
) -> None:
    docs = [await _upload(firm, case, synthetic_valid_pdf()) for _ in range(5)]
    engines = [create_engine(db_url("meip_app", TEST_DB), pool_size=1) for _ in range(8)]
    try:
        claims = await asyncio.gather(*(claim(create_sessionmaker(e), 600) for e in engines))
    finally:
        for engine in engines:
            await engine.dispose()
    claimed = [c.document_version_id for c in claims if c is not None]
    assert sorted(map(str, claimed)) == sorted(d["original"]["id"] for d in docs)
    assert sum(1 for c in claims if c is None) == 3


async def test_a_stale_worker_cannot_commit_after_its_claim_expired(
    firm: Firm, case: dict[str, Any], owner: asyncpg.Connection, worker: Worker
) -> None:
    sessionmaker, store, settings = worker
    doc = await _upload(firm, case, synthetic_valid_pdf())
    first = await claim(sessionmaker, 1)
    assert first is not None
    assert first.attempt == 1
    await owner.execute(  # the first worker stalls past its lock
        "UPDATE jobs SET locked_until = now() - interval '1 second' WHERE id = $1",
        first.job_id,
    )
    second = await claim(sessionmaker, 600)
    assert second is not None
    assert (second.job_id, second.attempt) == (first.job_id, 2)

    # The stale first worker does the work, but its fenced finish matches nothing.
    async with open_tenant_scope(sessionmaker, first.tenant_id) as scope:
        ctx = OperationContext(scope=scope, actor=WORKER_ACTOR)
        assert await ingest(ctx, store, first.document_version_id, settings) == "ready"
        assert await processing._finish(scope.session, first, JobStatus.DONE) is False
        # ... so it does not commit: the scope rolls back on exit.
    assert (await _document(firm, case, doc["id"]))["status"] == "received"
    events = (await firm.request("GET", f"/api/v1/cases/{case['id']}/events")).json()["items"]
    assert not [e for e in events if e["action"] == "document.processed"]

    # The current claim holder finishes normally.
    async with open_tenant_scope(sessionmaker, second.tenant_id) as scope:
        ctx = OperationContext(scope=scope, actor=WORKER_ACTOR)
        assert await ingest(ctx, store, second.document_version_id, settings) == "ready"
        assert await processing._finish(scope.session, second, JobStatus.DONE)
        await scope.session.commit()
    assert (await _document(firm, case, doc["id"]))["status"] == "ready"


async def test_transient_errors_retry_then_fail_as_exhausted(
    firm: Firm,
    case: dict[str, Any],
    owner: asyncpg.Connection,
    worker: Worker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessionmaker, store, settings = worker
    doc = await _upload(firm, case, synthetic_valid_pdf())
    version = doc["original"]["id"]

    async def unavailable(*args: Any, **kwargs: Any) -> None:
        raise OSError("storage unavailable")

    monkeypatch.setattr(store, "read_into", unavailable)
    for attempt in range(1, MAX_ATTEMPTS + 1):
        assert await run_once(sessionmaker, store, settings)
        job = await _job(owner, version)
        if attempt < MAX_ATTEMPTS:
            assert (job["status"], job["attempts"], job["error_code"]) == (
                "queued",
                attempt,
                "transient",
            )
            assert (await _document(firm, case, doc["id"]))["status"] == "received"
            await owner.execute(  # skip the one-minute delay
                "UPDATE jobs SET run_after = now() WHERE id = $1", job["id"]
            )
    job = await _job(owner, version)
    assert (job["status"], job["error_code"]) == ("failed", "retries_exhausted")
    out = await _document(firm, case, doc["id"])
    assert (out["status"], out["failure_code"]) == ("failed", "retries_exhausted")


async def test_a_job_whose_worker_kept_dying_is_failed_at_claim(
    firm: Firm, case: dict[str, Any], owner: asyncpg.Connection, worker: Worker
) -> None:
    doc = await _upload(firm, case, synthetic_valid_pdf())
    await owner.execute(  # three earlier claims, each worker died holding the lock
        "UPDATE jobs SET status = 'running', attempts = $2,"
        " locked_until = now() - interval '1 second' WHERE document_version_id = $1",
        uuid.UUID(doc["original"]["id"]),
        MAX_ATTEMPTS,
    )
    assert await run_once(*worker)
    out = await _document(firm, case, doc["id"])
    assert (out["status"], out["failure_code"]) == ("failed", "retries_exhausted")


# --- identity, fail-closed and the process ------------------------------------------------


def test_no_user_role_can_process_documents() -> None:
    assert all(Permission.DOCUMENT_PROCESS not in perms for perms in ROLE_PERMISSIONS.values())


async def test_ingest_requires_the_worker_capability(worker: Worker, firm: Firm) -> None:
    user = Actor.user(uuid.uuid4(), uuid.uuid4(), Role.FIRM_ADMIN)
    async with open_tenant_scope(worker[0], firm.tenant_id) as scope:
        with pytest.raises(ForbiddenError):
            await ingest(
                OperationContext(scope=scope, actor=user), worker[1], uuid.uuid4(), worker[2]
            )


async def test_worker_stops_when_asked(worker: Worker, owner: asyncpg.Connection) -> None:
    stop = asyncio.Event()
    stop.set()
    await asyncio.wait_for(run_worker(*worker, stop), timeout=5)


def test_worker_refuses_to_start_when_documents_are_disabled() -> None:
    env = {k: v for k, v in os.environ.items() if not k.startswith("MEIP_")}
    env.update({"MEIP_ENV": "test", "MEIP_AUTH_MODE": "dev"})
    result = subprocess.run(
        [sys.executable, "-m", "meip.documents.processing"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 2
    assert "not enabled" in result.stderr


def test_the_pdf_child_process_gets_no_secrets(
    monkeypatch: pytest.MonkeyPatch, tmp_dir: Path
) -> None:
    seen: dict[str, Any] = {}

    def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        seen.update(kwargs)
        return subprocess.CompletedProcess(args, 0, stdout='{"pages": 1}\n', stderr="")

    monkeypatch.setenv("MEIP_DATABASE_URL", "postgresql://secret")
    monkeypatch.setenv("MEIP_BLOB_CONNECTION_STRING", "AccountKey=secret")
    monkeypatch.setattr(subprocess, "run", fake_run)
    from meip.documents.pdfcheck import check_pdf

    assert check_pdf(tmp_dir / "x.pdf", 5).pages == 1
    assert not [k for k in seen["env"] if k.startswith("MEIP_")]
