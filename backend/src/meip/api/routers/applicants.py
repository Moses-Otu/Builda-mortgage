from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from meip.api.deps import ApplicantWriteCtx, CaseReadCtx, IfMatch, etag
from meip.applicants import service
from meip.applicants.schemas import (
    ApplicantCreate,
    ApplicantOut,
    ApplicantReinstatement,
    ApplicantRemoval,
    ApplicantUpdate,
)
from meip.firm import service as firm

router = APIRouter(prefix="/api/v1/cases/{case_id}/applicants", tags=["applicants"])


@router.get("", response_model=list[ApplicantOut], summary="List applicants on a case")
async def list_applicants(
    case_id: uuid.UUID,
    ctx: CaseReadCtx,
    include_removed: Annotated[
        bool, Query(description="Also list removed applicants (kept for traceability)")
    ] = False,
) -> list[ApplicantOut]:
    return await service.list_applicants(ctx, case_id, include_removed=include_removed)


@router.post(
    "",
    response_model=ApplicantOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add an applicant",
    description="The applicant takes the lowest free position (1 = first applicant).",
)
async def add_applicant(
    case_id: uuid.UUID,
    body: ApplicantCreate,
    ctx: ApplicantWriteCtx,
    response: Response,
) -> ApplicantOut:
    out = await service.add_applicant(ctx, case_id, body, await firm.max_applicants(ctx))
    await ctx.commit()
    response.headers["ETag"] = etag(out.version)
    return out


@router.get("/{applicant_id}", response_model=ApplicantOut, summary="Get an applicant")
async def get_applicant(
    case_id: uuid.UUID, applicant_id: uuid.UUID, ctx: CaseReadCtx, response: Response
) -> ApplicantOut:
    out = await service.get_applicant(ctx, case_id, applicant_id)
    response.headers["ETag"] = etag(out.version)
    return out


@router.patch("/{applicant_id}", response_model=ApplicantOut, summary="Update an applicant")
async def update_applicant(
    case_id: uuid.UUID,
    applicant_id: uuid.UUID,
    body: ApplicantUpdate,
    ctx: ApplicantWriteCtx,
    version: IfMatch,
    response: Response,
) -> ApplicantOut:
    out = await service.update_applicant(ctx, case_id, applicant_id, body, version)
    await ctx.commit()
    response.headers["ETag"] = etag(out.version)
    return out


@router.post(
    "/{applicant_id}/removal",
    response_model=ApplicantOut,
    summary="Remove an applicant from the case",
    description=(
        "Marks the applicant removed. Nothing is deleted or reassigned: documents, financial "
        "records and audit history stay traceable. Only while the case is editable."
    ),
)
async def remove_applicant(
    case_id: uuid.UUID,
    applicant_id: uuid.UUID,
    body: ApplicantRemoval,
    ctx: ApplicantWriteCtx,
    version: IfMatch,
    response: Response,
) -> ApplicantOut:
    out = await service.remove_applicant(ctx, case_id, applicant_id, body, version)
    await ctx.commit()
    response.headers["ETag"] = etag(out.version)
    return out


@router.post(
    "/{applicant_id}/reinstatement",
    response_model=ApplicantOut,
    summary="Reinstate a removed applicant",
    description=(
        "Returns the same applicant (same ID) to the case. Superseded or invalidated "
        "reviews are not revived."
    ),
)
async def reinstate_applicant(
    case_id: uuid.UUID,
    applicant_id: uuid.UUID,
    body: ApplicantReinstatement,
    ctx: ApplicantWriteCtx,
    version: IfMatch,
    response: Response,
) -> ApplicantOut:
    out = await service.reinstate_applicant(
        ctx, case_id, applicant_id, body, version, await firm.max_applicants(ctx)
    )
    await ctx.commit()
    response.headers["ETag"] = etag(out.version)
    return out
