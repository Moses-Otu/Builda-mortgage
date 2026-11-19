"""Reconciliation checks (Stage 2-A): computed on request over the case's current reviewed
payslips and declared figures; only a broker's acknowledgement of a discrepancy is stored."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Path, status

from meip.api.deps import CaseReadCtx, FinancialWriteCtx
from meip.reconciliation import service
from meip.reconciliation.schemas import AcknowledgementIn, ChecksOut, FindingOut

router = APIRouter(prefix="/api/v1/cases/{case_id}/checks", tags=["checks"])

FindingKey = Annotated[str, Path(pattern=r"^[0-9a-f]{64}$", description="A finding's key")]


@router.get(
    "",
    response_model=ChecksOut,
    summary="Run the checks",
    description=(
        "Runs both versioned rule sets over the case's current data: `payslip-checks/1` "
        "(reviewed payslips and declared figures) and `bank-checks/1` (confirmed bank "
        "statements: totals, running balance, gaps between statements, and whether each "
        "payslip's net pay arrived). Returns every finding: `passed`, `discrepancy` (values, "
        "difference and the sources cited) or `not_run` (with the reason). Each finding names "
        "its rule set. Nothing is stored. The checks never decide anything about the case, and "
        "a near salary match is never treated as received."
    ),
    responses={404: {"description": "Case not found"}},
)
async def get_checks(case_id: uuid.UUID, ctx: CaseReadCtx) -> ChecksOut:
    return await service.get_checks(ctx, case_id)


@router.post(
    "/{finding_key}/acknowledgement",
    response_model=FindingOut,
    status_code=status.HTTP_201_CREATED,
    summary="Acknowledge a discrepancy",
    description=(
        "Records that a broker has seen a current discrepancy, with a note. It changes no "
        "figures, does not resolve the discrepancy and approves no income: the finding is "
        "still reported as a discrepancy, with the acknowledgement attached. If any input "
        "behind the finding changes, it gets a new key and needs acknowledging again."
    ),
    responses={
        404: {"description": "Case not found"},
        409: {
            "description": "`finding-not-current` (no current discrepancy has this key) or "
            "`already-acknowledged`"
        },
        422: {"description": "Empty or too long note, or a closed case"},
    },
)
async def acknowledge(
    case_id: uuid.UUID, finding_key: FindingKey, body: AcknowledgementIn, ctx: FinancialWriteCtx
) -> FindingOut:
    out = await service.acknowledge(ctx, case_id, finding_key, body)
    await ctx.commit()
    return out
