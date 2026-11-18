"""Declared and broker-entered financial data for a case (Phase 1.1).

Items (income sources, commitments, expenditure) are versioned and use ETag/If-Match.
Observations are immutable: correct one by recording a new observation that supersedes it.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from meip.api.deps import CaseReadCtx, FinancialWriteCtx, IfMatch, etag
from meip.financial import service
from meip.financial.schemas import (
    CommitmentCreate,
    CommitmentOut,
    CommitmentUpdate,
    ExpenditureCreate,
    ExpenditureOut,
    ExpenditureUpdate,
    IncomeSourceCreate,
    IncomeSourceOut,
    IncomeSourceUpdate,
    MeasureOut,
    ObservationCreate,
    ObservationOut,
    ObservationRetirement,
    WithdrawalRequest,
)

router = APIRouter(prefix="/api/v1/cases/{case_id}/financial", tags=["financial"])
reference = APIRouter(prefix="/api/v1/financial", tags=["financial"])


@reference.get(
    "/measures",
    response_model=list[MeasureOut],
    summary="Measures and the value type each one takes",
)
async def list_measures(
    ctx: CaseReadCtx,
    include_deprecated: Annotated[
        bool, Query(description="Also list deprecated measures (still valid for old values)")
    ] = False,
) -> list[MeasureOut]:
    return await service.list_measures(ctx, include_deprecated=include_deprecated)


# --- income sources ----------------------------------------------------------------------


@router.get("/income-sources", response_model=list[IncomeSourceOut], summary="List income sources")
async def list_income_sources(
    case_id: uuid.UUID,
    ctx: CaseReadCtx,
    include_withdrawn: Annotated[
        bool, Query(description="Also list items withdrawn as entered in error")
    ] = False,
) -> list[IncomeSourceOut]:
    return await service.list_income_sources(ctx, case_id, include_withdrawn=include_withdrawn)


@router.post(
    "/income-sources",
    response_model=IncomeSourceOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add an income source for an active applicant",
)
async def create_income_source(
    case_id: uuid.UUID, body: IncomeSourceCreate, ctx: FinancialWriteCtx, response: Response
) -> IncomeSourceOut:
    out = await service.create_income_source(ctx, case_id, body)
    await ctx.commit()
    response.headers["ETag"] = etag(out.version)
    return out


@router.patch(
    "/income-sources/{income_source_id}",
    response_model=IncomeSourceOut,
    summary="Update an income source",
)
async def update_income_source(
    case_id: uuid.UUID,
    income_source_id: uuid.UUID,
    body: IncomeSourceUpdate,
    ctx: FinancialWriteCtx,
    version: IfMatch,
    response: Response,
) -> IncomeSourceOut:
    out = await service.update_income_source(ctx, case_id, income_source_id, body, version)
    await ctx.commit()
    response.headers["ETag"] = etag(out.version)
    return out


# --- commitments -------------------------------------------------------------------------


@router.get("/commitments", response_model=list[CommitmentOut], summary="List commitments")
async def list_commitments(
    case_id: uuid.UUID,
    ctx: CaseReadCtx,
    include_withdrawn: Annotated[
        bool, Query(description="Also list items withdrawn as entered in error")
    ] = False,
) -> list[CommitmentOut]:
    return await service.list_commitments(ctx, case_id, include_withdrawn=include_withdrawn)


@router.post(
    "/commitments",
    response_model=CommitmentOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add a commitment and the applicants liable for it",
)
async def create_commitment(
    case_id: uuid.UUID, body: CommitmentCreate, ctx: FinancialWriteCtx, response: Response
) -> CommitmentOut:
    out = await service.create_commitment(ctx, case_id, body)
    await ctx.commit()
    response.headers["ETag"] = etag(out.version)
    return out


@router.patch(
    "/commitments/{commitment_id}",
    response_model=CommitmentOut,
    summary="Update a commitment (parties, if sent, replace the list)",
)
async def update_commitment(
    case_id: uuid.UUID,
    commitment_id: uuid.UUID,
    body: CommitmentUpdate,
    ctx: FinancialWriteCtx,
    version: IfMatch,
    response: Response,
) -> CommitmentOut:
    out = await service.update_commitment(ctx, case_id, commitment_id, body, version)
    await ctx.commit()
    response.headers["ETag"] = etag(out.version)
    return out


# --- expenditure -------------------------------------------------------------------------


@router.get("/expenditure", response_model=list[ExpenditureOut], summary="List expenditure")
async def list_expenditure(
    case_id: uuid.UUID,
    ctx: CaseReadCtx,
    include_withdrawn: Annotated[
        bool, Query(description="Also list items withdrawn as entered in error")
    ] = False,
) -> list[ExpenditureOut]:
    return await service.list_expenditure(ctx, case_id, include_withdrawn=include_withdrawn)


@router.post(
    "/expenditure",
    response_model=ExpenditureOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add household or individual expenditure",
)
async def create_expenditure(
    case_id: uuid.UUID, body: ExpenditureCreate, ctx: FinancialWriteCtx, response: Response
) -> ExpenditureOut:
    out = await service.create_expenditure(ctx, case_id, body)
    await ctx.commit()
    response.headers["ETag"] = etag(out.version)
    return out


@router.patch(
    "/expenditure/{expenditure_item_id}",
    response_model=ExpenditureOut,
    summary="Update an expenditure item",
)
async def update_expenditure(
    case_id: uuid.UUID,
    expenditure_item_id: uuid.UUID,
    body: ExpenditureUpdate,
    ctx: FinancialWriteCtx,
    version: IfMatch,
    response: Response,
) -> ExpenditureOut:
    out = await service.update_expenditure(ctx, case_id, expenditure_item_id, body, version)
    await ctx.commit()
    response.headers["ETag"] = etag(out.version)
    return out


# --- observations ------------------------------------------------------------------------


@router.get(
    "/observations", response_model=list[ObservationOut], summary="List financial observations"
)
async def list_observations(
    case_id: uuid.UUID,
    ctx: CaseReadCtx,
    subject_id: Annotated[
        uuid.UUID | None, Query(description="Only observations of this item")
    ] = None,
    include_retired: Annotated[
        bool, Query(description="Also list superseded and retired observations")
    ] = False,
) -> list[ObservationOut]:
    return await service.list_observations(
        ctx, case_id, subject_id=subject_id, include_retired=include_retired
    )


@router.post(
    "/observations",
    response_model=ObservationOut,
    status_code=status.HTTP_201_CREATED,
    summary="Record a typed value for an item",
    description=(
        "The value's type must match the measure (see `GET /api/v1/financial/measures`). "
        "Set `supersedes_observation_id` to correct an earlier value; it is retired, not edited."
    ),
)
async def record_observation(
    case_id: uuid.UUID, body: ObservationCreate, ctx: FinancialWriteCtx
) -> ObservationOut:
    out = await service.record_observation(ctx, case_id, body)
    await ctx.commit()
    return out


@router.post(
    "/observations/{observation_id}/retirement",
    response_model=ObservationOut,
    summary="Retire an observation entered in error",
)
async def retire_observation(
    case_id: uuid.UUID,
    observation_id: uuid.UUID,
    body: ObservationRetirement,
    ctx: FinancialWriteCtx,
) -> ObservationOut:
    out = await service.retire_observation(ctx, case_id, observation_id, body)
    await ctx.commit()
    return out


@router.post(
    "/income-sources/{income_source_id}/withdrawal",
    response_model=IncomeSourceOut,
    summary="Withdraw an income source entered in error (irreversible)",
    description=(
        "Marks the item as never having been true and retires its active values, atomically. "
        "Nothing is deleted. To correct a withdrawal, create a new item."
    ),
)
async def withdraw_income_source(
    case_id: uuid.UUID,
    income_source_id: uuid.UUID,
    body: WithdrawalRequest,
    ctx: FinancialWriteCtx,
    version: IfMatch,
    response: Response,
) -> IncomeSourceOut:
    out = await service.withdraw_income_source(ctx, case_id, income_source_id, body, version)
    await ctx.commit()
    response.headers["ETag"] = etag(out.version)
    return out


@router.post(
    "/commitments/{commitment_id}/withdrawal",
    response_model=CommitmentOut,
    summary="Withdraw a commitment entered in error (irreversible)",
    description=(
        "Marks the item as never having been true and retires its active values, atomically. "
        "Nothing is deleted. To correct a withdrawal, create a new item."
    ),
)
async def withdraw_commitment(
    case_id: uuid.UUID,
    commitment_id: uuid.UUID,
    body: WithdrawalRequest,
    ctx: FinancialWriteCtx,
    version: IfMatch,
    response: Response,
) -> CommitmentOut:
    out = await service.withdraw_commitment(ctx, case_id, commitment_id, body, version)
    await ctx.commit()
    response.headers["ETag"] = etag(out.version)
    return out


@router.post(
    "/expenditure/{expenditure_item_id}/withdrawal",
    response_model=ExpenditureOut,
    summary="Withdraw an expenditure item entered in error (irreversible)",
    description=(
        "Marks the item as never having been true and retires its active values, atomically. "
        "Nothing is deleted. To correct a withdrawal, create a new item."
    ),
)
async def withdraw_expenditure(
    case_id: uuid.UUID,
    expenditure_item_id: uuid.UUID,
    body: WithdrawalRequest,
    ctx: FinancialWriteCtx,
    version: IfMatch,
    response: Response,
) -> ExpenditureOut:
    out = await service.withdraw_expenditure(ctx, case_id, expenditure_item_id, body, version)
    await ctx.commit()
    response.headers["ETag"] = etag(out.version)
    return out
