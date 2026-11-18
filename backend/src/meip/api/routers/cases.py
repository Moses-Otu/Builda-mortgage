from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status

from meip.api.deps import AuditReadCtx, CaseCreateCtx, CaseReadCtx, CaseTransitionCtx, CaseUpdateCtx
from meip.audit import service as audit
from meip.audit.schemas import AuditEventOut
from meip.cases import service
from meip.cases.models import CaseStatus
from meip.cases.schemas import CaseCreate, CaseOut, CaseTransition, CaseUpdate
from meip.core.schemas import Page

router = APIRouter(prefix="/api/v1/cases", tags=["cases"])

Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0)]


@router.get("", response_model=Page[CaseOut], summary="List cases")
async def list_cases(
    ctx: CaseReadCtx,
    status_: Annotated[CaseStatus | None, Query(alias="status")] = None,
    adviser_membership_id: uuid.UUID | None = None,
    limit: Limit = 25,
    offset: Offset = 0,
) -> Page[CaseOut]:
    items, total = await service.list_cases(
        ctx,
        status=status_,
        adviser_membership_id=adviser_membership_id,
        limit=limit,
        offset=offset,
    )
    return Page(items=items, total=total, limit=limit, offset=offset)


@router.post(
    "", response_model=CaseOut, status_code=status.HTTP_201_CREATED, summary="Create a case"
)
async def create_case(body: CaseCreate, ctx: CaseCreateCtx) -> CaseOut:
    out = await service.create_case(ctx, body)
    await ctx.commit()
    return out


@router.get("/{case_id}", response_model=CaseOut, summary="Get a case")
async def get_case(case_id: uuid.UUID, ctx: CaseReadCtx) -> CaseOut:
    return await service.get_case_out(ctx, case_id)


@router.patch("/{case_id}", response_model=CaseOut, summary="Update case details")
async def update_case(case_id: uuid.UUID, body: CaseUpdate, ctx: CaseUpdateCtx) -> CaseOut:
    out = await service.update_case(ctx, case_id, body)
    await ctx.commit()
    return out


@router.post(
    "/{case_id}/transitions",
    response_model=CaseOut,
    summary="Change case status",
    description=(
        "Moves the case through its lifecycle. Transitions that depend on capabilities not yet "
        "delivered return 409 with `available: false`."
    ),
)
async def transition_case(
    case_id: uuid.UUID, body: CaseTransition, ctx: CaseTransitionCtx
) -> CaseOut:
    out = await service.transition_case(ctx, case_id, body)
    await ctx.commit()
    return out


@router.get(
    "/{case_id}/events",
    response_model=Page[AuditEventOut],
    summary="Audit history for a case (oldest first)",
)
async def case_events(
    case_id: uuid.UUID, ctx: AuditReadCtx, limit: Limit = 50, offset: Offset = 0
) -> Page[AuditEventOut]:
    await service.get_case(ctx, case_id)
    events, total = await audit.list_for_case(ctx.session, case_id, limit=limit, offset=offset)
    return Page(
        items=[AuditEventOut.model_validate(e) for e in events],
        total=total,
        limit=limit,
        offset=offset,
    )
