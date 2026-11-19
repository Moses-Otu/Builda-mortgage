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
import io
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
from meip.documents.extraction import (
    CLASSIFIER_VERSION,
    SCHEMA_VERSION,
    ProviderError,
    build_layout_provider,
    classify,
    extract,
    raw_output_sha256,
)
from meip.documents.models import (
    Classification,
    Document,
    DocumentStatus,
    DocumentVersion,
    ExtractedField,
    ExtractionRun,
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
    await _enqueue(session, JobKind.DOCUMENT_INGEST, tenant_id, case_id, version_id)


async def _enqueue(
    session: AsyncSession,
    kind: JobKind,
    tenant_id: uuid.UUID,
    case_id: uuid.UUID,
    version_id: uuid.UUID,
) -> None:
    await session.execute(
        insert(Job)
        .values(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            case_id=case_id,
            kind=kind.value,
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
    await _enqueue(
        ctx.session, JobKind.DOCUMENT_EXTRACT, ctx.tenant_id, document.case_id, version.id
    )
    return DocumentStatus.READY.value


async def extract_document(
    ctx: OperationContext, store: BlobStore, version_id: uuid.UUID, settings: Settings
) -> str:
    """Classify and extract one ``ready`` document. Returns ``extracted``, ``unrecognised``,
    ``extraction_failed`` or ``skipped``. Raises :class:`TransientError` when worth retrying.

    Three distinct outcomes: extracted (run and fields stored), unrecognised (run stored with
    the markers found, no fields), provider failure (document failed, no run)."""
    ctx.require(Permission.DOCUMENT_PROCESS)
    loaded = await _load(ctx, version_id)
    if loaded is None:
        return "skipped"
    document, version = loaded
    if document.status != DocumentStatus.READY.value:
        return "skipped"  # already extracted or failed: running again changes nothing

    sink = io.BytesIO()
    try:
        await store.read_into(BlobKey(version.blob_key), sink)
    except Exception as exc:  # the original was verified at ingest: treat as transient
        raise TransientError(type(exc).__name__) from exc
    pdf = sink.getvalue()
    if hashlib.sha256(pdf).hexdigest() != version.sha256:
        raise TransientError("hash_changed")  # never extract from bytes we can't vouch for

    provider = build_layout_provider(settings)
    try:
        layout = await provider.analyse(pdf)
    except ProviderError:
        await _set_outcome(ctx, document, pages=None, failure=FailureCode.EXTRACTION_FAILED)
        return FailureCode.EXTRACTION_FAILED.value

    classification = classify(layout.text)
    run = ExtractionRun(
        tenant_id=ctx.tenant_id,
        case_id=document.case_id,
        document_version_id=version.id,
        provider=provider.name,
        model_version=provider.model_version,
        schema_version=SCHEMA_VERSION,
        classifier_version=CLASSIFIER_VERSION,
        classification=(
            Classification.PAYSLIP if classification.is_payslip else Classification.UNRECOGNISED
        ).value,
        markers=classification.markers,
        raw_output=layout.raw,
        raw_output_sha256=raw_output_sha256(layout.raw),
    )
    ctx.session.add(run)
    await ctx.session.flush()

    counts = {"field_count": 0, "payment_line_count": 0, "deduction_line_count": 0}
    if classification.is_payslip:
        for item in extract(layout).fields:
            page = layout.pages[item.value.page]
            ctx.session.add(
                ExtractedField(
                    tenant_id=ctx.tenant_id,
                    case_id=document.case_id,
                    run_id=run.id,
                    section=item.section.value,
                    ordinal=item.ordinal,
                    field_code=item.field_code,
                    label_text=item.label_text,
                    raw_text=item.raw_text,
                    value_money_minor=item.money_minor,
                    currency="GBP" if item.money_minor is not None else None,
                    value_date=item.value_date,
                    value_text=item.value_text,
                    page_number=page.number,
                    page_width=page.width,
                    page_height=page.height,
                    unit=layout.unit,
                    x0=item.value.box.x0,
                    y0=item.value.box.y0,
                    x1=item.value.box.x1,
                    y1=item.value.box.y1,
                    label_x0=item.label.box.x0,
                    label_y0=item.label.box.y0,
                    label_x1=item.label.box.x1,
                    label_y1=item.label.box.y1,
                    confidence=item.value.confidence,
                )
            )
            counts["field_count"] += 1
            if item.section.value == "payment":
                counts["payment_line_count"] += 1
            elif item.section.value == "deduction":
                counts["deduction_line_count"] += 1
        await ctx.session.flush()

    before = {"status": document.status}
    if classification.is_payslip:
        document.status = DocumentStatus.EXTRACTED.value
        action = AuditAction.DOCUMENT_EXTRACTED
    else:
        document.status = DocumentStatus.UNRECOGNISED.value
        action = AuditAction.DOCUMENT_UNRECOGNISED
    await ctx.session.flush()
    # Counts, versions and marker names only: no labels, values or document text.
    change_set = audit.diff(before, {"status": document.status})
    change_set.note("classification", run.classification)
    change_set.note("schema_version", SCHEMA_VERSION)
    change_set.note("markers", classification.markers)
    for name, value in counts.items():
        change_set.note(name, value)
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
    return document.status


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


# Per job kind: the handler, the document status it starts from, and the failure code
# recorded when its retries are exhausted.
HANDLERS = {
    JobKind.DOCUMENT_INGEST.value: (ingest, DocumentStatus.RECEIVED, FailureCode.RETRIES_EXHAUSTED),
    JobKind.DOCUMENT_EXTRACT.value: (
        extract_document,
        DocumentStatus.READY,
        FailureCode.EXTRACTION_FAILED,
    ),
}


async def _exhausted(ctx: OperationContext, job: ClaimedJob) -> str:
    _, starts_from, failure = HANDLERS[job.kind]
    loaded = await _load(ctx, job.document_version_id)
    if loaded is not None and loaded[0].status == starts_from.value:
        await _set_outcome(ctx, loaded[0], pages=None, failure=failure)
    return failure.value


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
                outcome, done = await _exhausted(ctx, job), JobStatus.FAILED
            else:
                handler = HANDLERS[job.kind][0]
                outcome = await handler(ctx, store, job.document_version_id, settings)
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
                code = await _exhausted(ctx, job)
                finished = await _finish(ctx.session, job, JobStatus.FAILED, code)
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
