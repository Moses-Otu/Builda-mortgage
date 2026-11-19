"""Document upload, listing and verified download (Phase 2.0, step 2-A).

The API never parses a PDF: it checks the declared type, the ``%PDF-`` signature at byte 0
and the size. Structure, encryption and page limits are checked in the sandboxed worker
(step 2-C). Uploads to one case are serialised by the case row lock, so a duplicate is
detected before anything is stored; the unique index on (tenant, case, SHA-256) is the
database guarantee underneath.
"""

from __future__ import annotations

import hashlib
import tempfile
import unicodedata
import urllib.parse
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import IO, Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from meip.applicants.models import Applicant, ApplicantStatus
from meip.audit import service as audit
from meip.audit.service import AuditAction
from meip.cases.service import get_case, require_editable
from meip.core.errors import (
    BusinessRuleError,
    ConflictError,
    IntegrityFailureError,
    NotFoundError,
    PayloadTooLargeError,
    UnsupportedMediaTypeError,
)
from meip.core.ids import uuid7
from meip.core.logging import get_logger
from meip.documents import statement_review
from meip.documents.extraction import REQUIRED_FIELDS
from meip.documents.models import (
    PDF_MEDIA_TYPE,
    Document,
    DocumentStatus,
    DocumentType,
    DocumentVersion,
    ExtractedField,
    ExtractedTransaction,
    ExtractionRun,
    FailureCode,
    ScanStatus,
)
from meip.documents.processing import enqueue_ingest
from meip.documents.schemas import (
    DocumentOut,
    DocumentVersionOut,
    Evidence,
    ExtractedFieldOut,
    ExtractionOut,
    TransactionCellsOut,
    TransactionOut,
)
from meip.documents.statements import REQUIRED_FIELDS as STATEMENT_REQUIRED
from meip.documents.storage import BlobKey, BlobNotFoundError, BlobStore
from meip.tenancy.context import OperationContext

PDF_SIGNATURE = b"%PDF-"
SPOOL_IN_MEMORY = 1024 * 1024  # larger bodies spill to a temporary file on disk
MAX_FILENAME = 255

_log = get_logger("meip.documents")


class DuplicateDocumentError(ConflictError):
    error_type = "duplicate-document"
    title = "This document is already on the case"


# --- receiving the body ------------------------------------------------------------------


@dataclass
class ReceivedFile:
    """An upload received in full, with its size and SHA-256 computed while streaming."""

    file: IO[bytes]
    size: int
    sha256: str

    def close(self) -> None:
        self.file.close()


def media_type(content_type: str | None) -> str:
    return (content_type or "").split(";", 1)[0].strip().lower()


def check_declared(content_type: str | None, content_length: str | None, max_bytes: int) -> None:
    """Reject before reading the body: wrong declared type (415) or declared size (413)."""
    if media_type(content_type) != PDF_MEDIA_TYPE:
        raise UnsupportedMediaTypeError("Upload a PDF with Content-Type: application/pdf")
    if content_length is not None:
        try:
            declared = int(content_length)
        except ValueError:
            declared = -1
        if declared > max_bytes:
            raise PayloadTooLargeError(f"The limit is {max_bytes} bytes")


async def receive_pdf(chunks: AsyncIterator[bytes], *, max_bytes: int) -> ReceivedFile:
    """Stream the body to a spooled temporary file, enforcing the size limit as bytes arrive
    (whatever Content-Length claimed), then check the PDF signature."""
    digest = hashlib.sha256()
    spool = tempfile.SpooledTemporaryFile(max_size=SPOOL_IN_MEMORY)  # noqa: SIM115 - returned
    size = 0
    try:
        async for chunk in chunks:
            size += len(chunk)
            if size > max_bytes:
                raise PayloadTooLargeError(f"The limit is {max_bytes} bytes")
            digest.update(chunk)
            spool.write(chunk)
        if size == 0:
            raise BusinessRuleError("The request body is empty")
        spool.seek(0)
        if spool.read(len(PDF_SIGNATURE)) != PDF_SIGNATURE:
            raise UnsupportedMediaTypeError("The file is not a PDF")
        spool.seek(0)
    except BaseException:
        spool.close()
        raise
    return ReceivedFile(file=spool, size=size, sha256=digest.hexdigest())


def clean_filename(raw: str | None) -> str | None:
    """A display name only: percent-decoded, path and control characters removed, capped."""
    if raw is None:
        return None
    name = urllib.parse.unquote(raw, errors="replace")
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(c for c in name if unicodedata.category(c)[0] != "C").strip()
    return name[:MAX_FILENAME] or None


# --- upload ------------------------------------------------------------------------------


async def _check_applicant(
    ctx: OperationContext, case_id: uuid.UUID, applicant_id: uuid.UUID | None
) -> None:
    if applicant_id is None:
        return
    applicant = await ctx.session.scalar(
        select(Applicant).where(Applicant.id == applicant_id, Applicant.case_id == case_id)
    )
    if applicant is None:
        raise BusinessRuleError("The applicant is not on this case")
    if applicant.status != ApplicantStatus.ACTIVE.value:
        raise ConflictError("The applicant has been removed from the case")


async def check_upload_target(
    ctx: OperationContext, case_id: uuid.UUID, applicant_id: uuid.UUID | None
) -> None:
    """Checked before the body is read, so a foreign or closed case costs no upload."""
    require_editable(await get_case(ctx, case_id))
    await _check_applicant(ctx, case_id, applicant_id)


async def _existing_document(ctx: OperationContext, case_id: uuid.UUID, sha256: str) -> Any:
    return await ctx.session.scalar(
        select(DocumentVersion.document_id).where(
            DocumentVersion.case_id == case_id, DocumentVersion.sha256 == sha256
        )
    )


def _duplicate(document_id: uuid.UUID) -> DuplicateDocumentError:
    return DuplicateDocumentError(
        "A document with identical content is already on this case",
        extra={"existing_document_id": str(document_id)},
    )


DUPLICATE_CONSTRAINT = "uq_document_versions_tenant_id_case_id_sha256"
_UNIQUE_VIOLATION = "23505"


def _is_duplicate_violation(exc: IntegrityError) -> bool:
    """Only the content-uniqueness index: not, say, the SHA-256 format CHECK."""
    orig: Any = exc.orig
    for candidate in (orig, getattr(orig, "__cause__", None)):
        state = getattr(candidate, "sqlstate", None) or getattr(candidate, "pgcode", None)
        name = getattr(candidate, "constraint_name", None)
        if state == _UNIQUE_VIOLATION and name == DUPLICATE_CONSTRAINT:
            return True
    return False


async def store_upload(
    ctx: OperationContext,
    store: BlobStore,
    case_id: uuid.UUID,
    received: ReceivedFile,
    *,
    document_type: DocumentType,
    applicant_id: uuid.UUID | None,
    filename: str | None,
) -> DocumentOut:
    # The case lock serialises uploads to this case: the duplicate check below cannot race,
    # and nothing is stored for a duplicate.
    require_editable(await get_case(ctx, case_id, for_update=True))
    await _check_applicant(ctx, case_id, applicant_id)
    existing = await _existing_document(ctx, case_id, received.sha256)
    if existing is not None:
        raise _duplicate(existing)

    document_id, version_id = uuid7(), uuid7()
    key = BlobKey.original(ctx.tenant_id, case_id, document_id, version_id)
    received.file.seek(0)
    await store.put_new(key, received.file)

    document = Document(
        id=document_id,
        tenant_id=ctx.tenant_id,
        case_id=case_id,
        applicant_id=applicant_id,
        document_type=document_type.value,
        status=DocumentStatus.RECEIVED.value,
        created_by_user_id=ctx.actor.user_id,
    )
    original = DocumentVersion(
        id=version_id,
        tenant_id=ctx.tenant_id,
        case_id=case_id,
        document_id=document_id,
        version_number=1,
        sha256=received.sha256,
        size_bytes=received.size,
        media_type=PDF_MEDIA_TYPE,
        blob_key=key.value,
        original_filename=filename,
        scan_status=ScanStatus.NOT_SCANNED.value,
        uploaded_by_user_id=ctx.actor.user_id,
    )
    try:
        async with ctx.session.begin_nested():
            ctx.session.add(document)
            await ctx.session.flush()
            ctx.session.add(original)
            await ctx.session.flush()
    except IntegrityError as exc:
        # Only reachable by a writer that bypassed the case lock: the database still refuses
        # the duplicate. The stored blob is then an orphan (see find-orphan-blobs).
        if not _is_duplicate_violation(exc):
            raise
        raise _duplicate(await _existing_document(ctx, case_id, received.sha256)) from exc

    # Same transaction as the upload: no job without its document, no document without its job.
    await enqueue_ingest(ctx.session, ctx.tenant_id, case_id, version_id)

    change_set = audit.creation(
        {
            "document_type": document.document_type,
            "applicant_id": applicant_id,
            "status": document.status,
        }
    )
    for name, value in (
        ("document_version_id", version_id),
        ("size_bytes", received.size),
        ("media_type", PDF_MEDIA_TYPE),
        ("sha256", received.sha256),  # erasable: could confirm a known document was held
        ("original_filename", filename),  # erasable personal data
    ):
        change_set.note(name, value)
    await audit.record(
        ctx.session,
        tenant_id=ctx.tenant_id,
        actor=ctx.actor,
        action=AuditAction.DOCUMENT_UPLOADED,
        entity_type="document",
        entity_id=document_id,
        case_id=case_id,
        change_set=change_set,
    )
    return _out(document, original)


# --- reading -----------------------------------------------------------------------------


def _out(document: Document, original: DocumentVersion) -> DocumentOut:
    return DocumentOut(
        id=document.id,
        case_id=document.case_id,
        applicant_id=document.applicant_id,
        document_type=DocumentType(document.document_type),
        status=DocumentStatus(document.status),
        created_by_user_id=document.created_by_user_id,
        created_at=document.created_at,
        version=document.version,
        failure_code=FailureCode(document.failure_code) if document.failure_code else None,
        page_count=document.page_count,
        original=DocumentVersionOut(
            id=original.id,
            version_number=original.version_number,
            sha256=original.sha256,
            size_bytes=original.size_bytes,
            media_type=original.media_type,
            original_filename=original.original_filename,
            scan_status=ScanStatus(original.scan_status),
            uploaded_by_user_id=original.uploaded_by_user_id,
            uploaded_at=original.uploaded_at,
        ),
    )


async def list_documents(ctx: OperationContext, case_id: uuid.UUID) -> list[DocumentOut]:
    await get_case(ctx, case_id)
    rows = await ctx.session.execute(
        select(Document, DocumentVersion)
        .join(DocumentVersion, DocumentVersion.document_id == Document.id)
        .where(Document.case_id == case_id, DocumentVersion.version_number == 1)
        .order_by(Document.created_at, Document.id)
    )
    return [_out(document, original) for document, original in rows]


async def _load(
    ctx: OperationContext, case_id: uuid.UUID, document_id: uuid.UUID
) -> tuple[Document, DocumentVersion]:
    row = (
        await ctx.session.execute(
            select(Document, DocumentVersion)
            .join(DocumentVersion, DocumentVersion.document_id == Document.id)
            .where(
                Document.id == document_id,
                Document.case_id == case_id,
                DocumentVersion.version_number == 1,
            )
        )
    ).one_or_none()
    if row is None:
        raise NotFoundError("Document not found")
    document, original = row
    return document, original


async def get_document(
    ctx: OperationContext, case_id: uuid.UUID, document_id: uuid.UUID
) -> DocumentOut:
    return _out(*await _load(ctx, case_id, document_id))


async def open_original(
    ctx: OperationContext, store: BlobStore, case_id: uuid.UUID, document_id: uuid.UUID
) -> tuple[DocumentVersion, IO[bytes]]:
    """The stored original, read in full and checked against its recorded SHA-256 before a
    single byte is returned. The database row (under row-level security) is always looked up
    first; a storage key is never taken from the client."""
    _, original = await _load(ctx, case_id, document_id)
    spool = tempfile.SpooledTemporaryFile(max_size=SPOOL_IN_MEMORY)  # noqa: SIM115 - returned
    try:
        try:
            await store.read_into(BlobKey(original.blob_key), spool)
        except BlobNotFoundError:
            _log.error("document_integrity_failure", document_id=str(document_id), reason="missing")
            raise IntegrityFailureError() from None
        spool.seek(0)
        digest = hashlib.sha256()
        while chunk := spool.read(1024 * 1024):
            digest.update(chunk)
        if digest.hexdigest() != original.sha256:
            _log.error(
                "document_integrity_failure", document_id=str(document_id), reason="hash_mismatch"
            )
            raise IntegrityFailureError()
        spool.seek(0)
    except BaseException:
        spool.close()
        raise
    return original, spool


# --- extraction results --------------------------------------------------------------------


def _field_out(f: ExtractedField) -> ExtractedFieldOut:
    return ExtractedFieldOut(
        ordinal=f.ordinal,
        field_code=f.field_code,
        label_text=f.label_text,
        raw_text=f.raw_text,
        value_money_minor=f.value_money_minor,
        currency=f.currency,
        value_date=f.value_date,
        value_text=f.value_text,
        value_evidence=Evidence(page_number=f.page_number, x0=f.x0, y0=f.y0, x1=f.x1, y1=f.y1),
        label_evidence=Evidence(
            page_number=f.page_number, x0=f.label_x0, y0=f.label_y0, x1=f.label_x1, y1=f.label_y1
        ),
        page_width=f.page_width,
        page_height=f.page_height,
        unit=f.unit,
        confidence=f.confidence,
    )


async def get_extraction(
    ctx: OperationContext, case_id: uuid.UUID, document_id: uuid.UUID
) -> ExtractionOut:
    _, original = await _load(ctx, case_id, document_id)
    run = await ctx.session.scalar(
        select(ExtractionRun).where(ExtractionRun.document_version_id == original.id)
    )
    if run is None:
        raise NotFoundError("No extraction for this document yet")
    fields = list(
        await ctx.session.scalars(
            select(ExtractedField)
            .where(ExtractedField.run_id == run.id)
            .order_by(ExtractedField.ordinal)
        )
    )
    by_section: dict[str, list[ExtractedFieldOut]] = {
        "summary": [],
        "payment": [],
        "deduction": [],
        "year_to_date": [],
    }
    for f in fields:
        by_section[f.section].append(_field_out(f))
    parsed = {
        f.field_code
        for f in fields
        if f.value_money_minor is not None or f.value_date is not None or f.value_text
    }
    is_payslip = run.classification == "payslip"
    is_statement = run.classification == "bank_statement"
    required = REQUIRED_FIELDS if is_payslip else STATEMENT_REQUIRED if is_statement else ()
    transactions = (
        [
            _transaction_out(t)
            for t in await ctx.session.scalars(
                select(ExtractedTransaction)
                .where(ExtractedTransaction.run_id == run.id)
                .order_by(ExtractedTransaction.row_number)
            )
        ]
        if is_statement
        else []
    )
    return ExtractionOut(
        provider=run.provider,
        model_version=run.model_version,
        schema_version=run.schema_version,
        classifier_version=run.classifier_version,
        classification=run.classification,
        markers=list(run.markers),
        created_at=run.created_at,
        summary=by_section["summary"],
        payments=by_section["payment"],
        deductions=by_section["deduction"],
        year_to_date=by_section["year_to_date"],
        missing_required=[c for c in required if c not in parsed],
        transactions=transactions,
        integrity=(
            await statement_review.extracted_integrity(ctx, run.id, fields)
            if is_statement
            else None
        ),
        account_match=(
            await statement_review.account_match(
                ctx,
                case_id,
                run,
                next((f.value_text for f in fields if f.field_code == "account_ending"), None),
            )
            if is_statement
            else None
        ),
    )


def _transaction_out(t: ExtractedTransaction) -> TransactionOut:
    return TransactionOut(
        row_number=t.row_number,
        kind=t.kind,
        posted_on=t.posted_on,
        description=t.description,
        description_truncated=t.description_truncated,
        paid_in_minor=t.paid_in_minor,
        paid_out_minor=t.paid_out_minor,
        balance_minor=t.balance_minor,
        cells=TransactionCellsOut.model_validate(t.cells),
        row_evidence=Evidence(page_number=t.page_number, x0=t.x0, y0=t.y0, x1=t.x1, y1=t.y1),
        page_width=t.page_width,
        page_height=t.page_height,
        unit=t.unit,
    )
