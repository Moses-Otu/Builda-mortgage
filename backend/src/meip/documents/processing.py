"""Background processing of uploaded documents (Phase 2.0, step 2-B, minimum viable).

* **Enqueue** happens in the upload's own transaction (:func:`enqueue_ingest`), so a job can
  never refer to a document that was not committed. ``UNIQUE (kind, document_version_id)``
  makes enqueueing idempotent.
* **Claim** goes through ``app_claim_job()`` (``FOR UPDATE SKIP LOCKED``): concurrent
  workers never receive the same job. A job whose lock expired (its worker died) can be
  claimed again, and each claim increments ``attempts``.
* **Finish** happens in the same transaction as the job's writes and is fenced on the
  claimed attempt number: if another worker has claimed the job since, nothing is updated
  and the whole transaction rolls back. At most one attempt's effects are ever committed.
* **Ingest** re-checks the original's SHA-256, opens the PDF in a separate time-limited
  process (:mod:`meip.documents.pdfcheck`), and marks the document ``ready`` or ``failed``.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import signal
import sys
import tempfile
import uuid
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any, cast

import anyio.to_thread
from sqlalchemy import CursorResult, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from meip.audit import service as audit
from meip.audit.service import AuditAction
from meip.core.config import Settings, get_settings
from meip.core.db import create_engine, create_sessionmaker, utcnow
from meip.core.logging import configure_logging, get_logger
from meip.documents.models import (
    Document,
    DocumentStatus,
    DocumentVersion,
    FailureCode,
    Job,
    JobKind,
    JobStatus,
)
from meip.documents.pdfcheck import check_pdf
from meip.documents.storage import BlobKey, BlobNotFoundError, BlobStore, build_blob_store
from meip.tenancy.context import Actor, OperationContext, TenantScope, open_tenant_scope
from meip.tenancy.permissions import Permission

MAX_ATTEMPTS = 3
RETRY_DELAY = timedelta(minutes=1)
WORKER_ACTOR = Actor.system("worker", capabilities=frozenset({Permission.DOCUMENT_PROCESS}))

_log = get_logger("meip.documents.worker")


# --- queue -------------------------------------------------------------------------------


async def enqueue_ingest(
    session: AsyncSession, tenant_id: uuid.UUID, case_id: uuid.UUID, version_id: uuid.UUID
) -> None:
    """Queue the ingest job in the caller's transaction (idempotent)."""
    await session.execute(
        insert(Job)
        .values(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            case_id=case_id,
            kind=JobKind.DOCUMENT_INGEST.value,
            document_version_id=version_id,
            status=JobStatus.QUEUED.value,
            attempts=0,
            run_after=utcnow(),
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        .on_conflict_do_nothing(index_elements=["kind", "document_version_id"])
    )


@dataclass(frozen=True)
class ClaimedJob:
    job_id: uuid.UUID
    tenant_id: uuid.UUID
    kind: str
    document_version_id: uuid.UUID
    attempt: int


async def claim(
    sessionmaker: async_sessionmaker[AsyncSession], lock_seconds: int
) -> ClaimedJob | None:
    async with sessionmaker() as session, session.begin():
        row = (
            await session.execute(
                text("SELECT * FROM app_claim_job(:lock)"), {"lock": lock_seconds}
            )
        ).one_or_none()
    if row is None:
        return None
    return ClaimedJob(row.job_id, row.tenant_id, row.kind, row.document_version_id, row.attempts)


async def _finish(
    session: AsyncSession,
    job: ClaimedJob,
    status: JobStatus,
    error_code: str | None = None,
    retry_after: timedelta | None = None,
) -> bool:
    """Record the outcome, only if this worker's claim is still the current one."""
    values: dict[str, object] = {
        "status": status.value,
        "error_code": error_code,
        "locked_until": None,
        "updated_at": utcnow(),
    }
    if retry_after is not None:
        values["run_after"] = utcnow() + retry_after
    result = await session.execute(
        update(Job)
        .where(
            Job.id == job.job_id,
            Job.status == JobStatus.RUNNING.value,
            Job.attempts == job.attempt,
        )
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    return cast("CursorResult[Any]", result).rowcount == 1


# --- ingest ------------------------------------------------------------------------------


class TransientError(Exception):
    """A failure worth retrying (storage unavailable, ...)."""


async def _set_outcome(
    ctx: OperationContext, document: Document, *, pages: int | None, failure: str | None
) -> None:
    before = {"status": document.status}
    if failure is None:
        document.status = DocumentStatus.READY.value
        document.page_count = pages
        action = AuditAction.DOCUMENT_PROCESSED
    else:
        document.status = DocumentStatus.FAILED.value
        document.failure_code = failure
        action = AuditAction.DOCUMENT_PROCESSING_FAILED
    await ctx.session.flush()
    change_set = audit.diff(before, {"status": document.status})
    change_set.note("page_count", document.page_count).note("failure_code", failure)
    await audit.record(
        ctx.session,
        tenant_id=ctx.tenant_id,
        actor=ctx.actor,
        action=action,
        entity_type="document",
        entity_id=document.id,
        case_id=document.case_id,
        change_set=change_set,
        correlation_id=ctx.correlation_id,
    )


async def _load(
    ctx: OperationContext, version_id: uuid.UUID
) -> tuple[Document, DocumentVersion] | None:
    row = (
        await ctx.session.execute(
            select(Document, DocumentVersion)
            .join(DocumentVersion, DocumentVersion.document_id == Document.id)
            .where(DocumentVersion.id == version_id)
            .with_for_update(of=Document)
        )
    ).one_or_none()
    if row is None:
        return None
    document, version = row
    return document, version


async def ingest(
    ctx: OperationContext, store: BlobStore, version_id: uuid.UUID, settings: Settings
) -> str:
    """Check one uploaded original. Returns ``ready``, a failure code, or ``skipped``.
    Raises :class:`TransientError` for failures worth retrying."""
    ctx.require(Permission.DOCUMENT_PROCESS)
    loaded = await _load(ctx, version_id)
    if loaded is None:  # not visible in this tenant: never act on it
        return "skipped"
    document, version = loaded
    if document.status != DocumentStatus.RECEIVED.value:
        return "skipped"  # already processed: running again changes nothing

    with tempfile.TemporaryDirectory(prefix="meip-ingest-") as folder:
        path = Path(folder) / "original.pdf"
        try:
            with path.open("wb") as sink:
                await store.read_into(BlobKey(version.blob_key), sink)
        except BlobNotFoundError:
            await _set_outcome(ctx, document, pages=None, failure=FailureCode.MISSING_ORIGINAL)
            return FailureCode.MISSING_ORIGINAL.value
        except Exception as exc:
            raise TransientError(type(exc).__name__) from exc

        digest = await anyio.to_thread.run_sync(_sha256, path)
        if digest != version.sha256:
            await _set_outcome(ctx, document, pages=None, failure=FailureCode.HASH_MISMATCH)
            return FailureCode.HASH_MISMATCH.value

        result = await anyio.to_thread.run_sync(check_pdf, path, settings.pdf_check_timeout_seconds)
    if result.error is not None:
        await _set_outcome(ctx, document, pages=None, failure=FailureCode(result.error))
        return result.error
    await _set_outcome(ctx, document, pages=result.pages, failure=None)
    return DocumentStatus.READY.value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


async def _exhausted(ctx: OperationContext, version_id: uuid.UUID) -> None:
    loaded = await _load(ctx, version_id)
    if loaded is not None and loaded[0].status == DocumentStatus.RECEIVED.value:
        await _set_outcome(ctx, loaded[0], pages=None, failure=FailureCode.RETRIES_EXHAUSTED)


# --- worker ------------------------------------------------------------------------------


async def run_once(
    sessionmaker: async_sessionmaker[AsyncSession], store: BlobStore, settings: Settings
) -> bool:
    """Claim and process one job. Returns False when there was nothing to do."""
    job = await claim(sessionmaker, settings.worker_lock_seconds)
    if job is None:
        return False

    def context(scope: TenantScope) -> OperationContext:
        return OperationContext(scope=scope, actor=WORKER_ACTOR, correlation_id=str(job.job_id))

    outcome = "unexpected"
    retry = False
    async with open_tenant_scope(sessionmaker, job.tenant_id) as scope:
        ctx = context(scope)
        try:
            if job.attempt > MAX_ATTEMPTS:
                await _exhausted(ctx, job.document_version_id)
                outcome, done = FailureCode.RETRIES_EXHAUSTED.value, JobStatus.FAILED
            else:
                outcome = await ingest(ctx, store, job.document_version_id, settings)
                done = JobStatus.DONE
            if await _finish(ctx.session, job, done, None if done is JobStatus.DONE else outcome):
                await ctx.commit()
            else:
                outcome = "lost_claim"  # another worker owns the job now: commit nothing
        except Exception as exc:
            retry = True
            outcome = "transient" if isinstance(exc, TransientError) else "unexpected"
            _log.warning("job_error", job_id=str(job.job_id), error_type=type(exc).__name__)

    if retry:  # a fresh transaction: the failed one was rolled back
        async with open_tenant_scope(sessionmaker, job.tenant_id) as scope:
            ctx = context(scope)
            if job.attempt >= MAX_ATTEMPTS:
                await _exhausted(ctx, job.document_version_id)
                finished = await _finish(
                    ctx.session, job, JobStatus.FAILED, FailureCode.RETRIES_EXHAUSTED.value
                )
            else:
                finished = await _finish(
                    ctx.session, job, JobStatus.QUEUED, outcome, retry_after=RETRY_DELAY
                )
            if finished:
                await ctx.commit()
    _log.info("job_finished", job_id=str(job.job_id), kind=job.kind, outcome=outcome)
    return True


async def run_worker(
    sessionmaker: async_sessionmaker[AsyncSession],
    store: BlobStore,
    settings: Settings,
    stop: asyncio.Event,
) -> None:
    """Process jobs until ``stop`` is set; the current job always finishes first."""
    while not stop.is_set():
        if not await run_once(sessionmaker, store, settings):
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=settings.worker_poll_seconds)


def main() -> int:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    if not settings.documents_enabled:  # fail closed, like the API
        print("Documents are not enabled (MEIP_DOCUMENTS_ENABLED); not starting", file=sys.stderr)
        return 2

    async def _run() -> None:
        engine = create_engine(settings.database_url, pool_size=2)
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            # Windows has no signal handlers here; Ctrl+C still raises KeyboardInterrupt.
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(sig, stop.set)
        _log.info("worker_started")
        try:
            await run_worker(
                create_sessionmaker(engine), build_blob_store(settings), settings, stop
            )
        finally:
            await engine.dispose()

    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
