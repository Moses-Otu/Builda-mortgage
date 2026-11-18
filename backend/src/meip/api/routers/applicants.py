from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status

from meip.api.deps import ApplicantWriteCtx, CaseReadCtx, get_settings
from meip.applicants import service
from meip.applicants.schemas import ApplicantCreate, ApplicantOut, ApplicantUpdate
from meip.core.config import Settings

router = APIRouter(prefix="/api/v1/cases/{case_id}/applicants", tags=["applicants"])


@router.get("", response_model=list[ApplicantOut], summary="List applicants on a case")
async def list_applicants(case_id: uuid.UUID, ctx: CaseReadCtx) -> list[ApplicantOut]:
    return await service.list_applicants(ctx, case_id)


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
    settings: Annotated[Settings, Depends(get_settings)],
) -> ApplicantOut:
    out = await service.add_applicant(ctx, case_id, body, settings.max_applicants_per_case)
    await ctx.commit()
    return out


@router.get("/{applicant_id}", response_model=ApplicantOut, summary="Get an applicant")
async def get_applicant(
    case_id: uuid.UUID, applicant_id: uuid.UUID, ctx: CaseReadCtx
) -> ApplicantOut:
    return await service.get_applicant(ctx, case_id, applicant_id)


@router.patch("/{applicant_id}", response_model=ApplicantOut, summary="Update an applicant")
async def update_applicant(
    case_id: uuid.UUID, applicant_id: uuid.UUID, body: ApplicantUpdate, ctx: ApplicantWriteCtx
) -> ApplicantOut:
    out = await service.update_applicant(ctx, case_id, applicant_id, body)
    await ctx.commit()
    return out


@router.delete(
    "/{applicant_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove an applicant",
    description="Only while the case is editable. The audit history is retained.",
)
async def remove_applicant(
    case_id: uuid.UUID,
    applicant_id: uuid.UUID,
    ctx: ApplicantWriteCtx,
    version: Annotated[int, Query(ge=1, description="Current applicant version")],
) -> Response:
    await service.remove_applicant(ctx, case_id, applicant_id, version)
    await ctx.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
