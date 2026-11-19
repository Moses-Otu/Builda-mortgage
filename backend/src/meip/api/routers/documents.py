"""Documents: upload a PDF to a case, list documents, download a verified original.

Disabled (404 ``feature-disabled``) unless the deployment enables documents. Uploads are the
raw PDF body (``Content-Type: application/pdf``), so the size limit is enforced while the
body streams. Personal data never travels in the URL: the optional original file name goes
in the ``Upload-Filename`` header (percent-encoded).
"""

from __future__ import annotations

import base64
import uuid
from collections.abc import Iterator
from typing import IO, Annotated

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from meip.api.deps import (
    DocumentReadCtx,
    DocumentWriteCtx,
    FinancialWriteCtx,
    IfMatch,
    SettingsDep,
    Store,
    etag,
    require_documents_enabled,
)
from meip.documents import review, service
from meip.documents.models import PDF_MEDIA_TYPE, DocumentType
from meip.documents.schemas import DocumentOut, ExtractionOut, ReviewIn, ReviewOut

router = APIRouter(
    prefix="/api/v1/cases/{case_id}/documents",
    tags=["documents"],
    dependencies=[Depends(require_documents_enabled)],
)

_PDF_BODY = {
    "requestBody": {
        "required": True,
        "content": {PDF_MEDIA_TYPE: {"schema": {"type": "string", "format": "binary"}}},
    }
}


@router.post(
    "",
    response_model=DocumentOut,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a document (PDF)",
    description=(
        "The body is the PDF itself. Identical content already on the case returns 409 "
        "`duplicate-document` with `existing_document_id`. Only synthetic documents may be "
        "used until the retention decision (D10) is approved."
    ),
    openapi_extra=_PDF_BODY,
    responses={409: {"description": "Duplicate content"}, 413: {}, 415: {}},
)
async def upload_document(
    case_id: uuid.UUID,
    request: Request,
    ctx: DocumentWriteCtx,
    store: Store,
    settings: SettingsDep,
    response: Response,
    document_type: Annotated[DocumentType, Query(description="Phase 2.0: payslip only")],
    applicant_id: Annotated[
        uuid.UUID | None, Query(description="The applicant the document belongs to")
    ] = None,
    content_type: Annotated[str | None, Header(alias="Content-Type")] = None,
    content_length: Annotated[str | None, Header(alias="Content-Length")] = None,
    upload_filename: Annotated[
        str | None,
        Header(
            alias="Upload-Filename",
            description="Optional original file name, percent-encoded. Never logged.",
        ),
    ] = None,
) -> DocumentOut:
    service.check_declared(content_type, content_length, settings.document_max_bytes)
    await service.check_upload_target(ctx, case_id, applicant_id)
    received = await service.receive_pdf(request.stream(), max_bytes=settings.document_max_bytes)
    try:
        out = await service.store_upload(
            ctx,
            store,
            case_id,
            received,
            document_type=document_type,
            applicant_id=applicant_id,
            filename=service.clean_filename(upload_filename),
        )
        await ctx.commit()
    finally:
        received.close()
    response.headers["ETag"] = etag(out.version)
    return out


@router.get("", response_model=list[DocumentOut], summary="List documents on a case")
async def list_documents(case_id: uuid.UUID, ctx: DocumentReadCtx) -> list[DocumentOut]:
    return await service.list_documents(ctx, case_id)


@router.get("/{document_id}", response_model=DocumentOut, summary="Get a document's details")
async def get_document(
    case_id: uuid.UUID, document_id: uuid.UUID, ctx: DocumentReadCtx, response: Response
) -> DocumentOut:
    out = await service.get_document(ctx, case_id, document_id)
    response.headers["ETag"] = etag(out.version)
    return out


def _chunks(file: IO[bytes]) -> Iterator[bytes]:
    while chunk := file.read(64 * 1024):
        yield chunk


@router.get(
    "/{document_id}/content",
    response_class=StreamingResponse,
    summary="Download the original",
    description=(
        "The stored bytes are checked against the recorded SHA-256 before anything is sent; "
        "a mismatch returns 500 `integrity-error`. `Repr-Digest` carries the hash."
    ),
    responses={200: {"content": {PDF_MEDIA_TYPE: {}}, "description": "The original PDF"}},
)
async def download_original(
    case_id: uuid.UUID, document_id: uuid.UUID, ctx: DocumentReadCtx, store: Store
) -> StreamingResponse:
    original, file = await service.open_original(ctx, store, case_id, document_id)
    digest = base64.b64encode(bytes.fromhex(original.sha256)).decode("ascii")
    return StreamingResponse(
        _chunks(file),
        media_type=PDF_MEDIA_TYPE,
        headers={
            "Content-Length": str(original.size_bytes),
            "Content-Disposition": f'attachment; filename="document-{document_id}.pdf"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
            "Repr-Digest": f"sha-256=:{digest}:",
        },
        background=BackgroundTask(file.close),
    )


@router.get(
    "/{document_id}/extraction",
    response_model=ExtractionOut,
    summary="Get the classification and extracted fields",
    description=(
        "Each value has its label, raw text, normalised value and evidence (page and boxes). "
        "Confidence is an indicator only. 404 until extraction has run. "
        "`provider = synthetic-fixture` marks results from synthetic test data."
    ),
)
async def get_extraction(
    case_id: uuid.UUID, document_id: uuid.UUID, ctx: DocumentReadCtx
) -> ExtractionOut:
    return await service.get_extraction(ctx, case_id, document_id)


@router.post(
    "/{document_id}/review",
    response_model=ReviewOut,
    status_code=status.HTTP_201_CREATED,
    summary="Review the extracted fields and save the approved figures",
    description=(
        "One decision (accept, correct or reject) for every extracted field. Accepted and "
        "corrected financial fields are saved as figures on an existing income source; "
        "descriptive fields (dates, frequency, tax code) are kept as reviewed evidence. "
        "All or nothing. A document is reviewed once: a second submission returns 409 "
        "`already-reviewed` with the existing review's ID."
    ),
    responses={
        404: {"description": "Case or document not found"},
        409: {
            "description": "`already-reviewed` (with `existing_review_id`), or the document "
            "is not extracted"
        },
        412: {"description": "If-Match does not match the document's current version"},
        428: {"description": "If-Match header missing"},
    },
)
async def submit_review(
    case_id: uuid.UUID,
    document_id: uuid.UUID,
    body: ReviewIn,
    ctx: FinancialWriteCtx,
    version: IfMatch,
) -> ReviewOut:
    out = await review.submit_review(ctx, case_id, document_id, body, version)
    await ctx.commit()
    return out


@router.get(
    "/{document_id}/review",
    response_model=ReviewOut,
    summary="Get the saved review",
    description="Each decision with the extracted and corrected values and the figure saved.",
)
async def get_review(case_id: uuid.UUID, document_id: uuid.UUID, ctx: DocumentReadCtx) -> ReviewOut:
    return await review.get_review(ctx, case_id, document_id)
