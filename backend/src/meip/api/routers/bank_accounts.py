"""Bank accounts on a case (Stage 2-B): created when a broker confirms a statement. Only the
last 4 digits are ever returned, never an account number or sort code. Like statements, these
routes are refused outside local and test (404 ``feature-disabled``)."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status

from meip.api.deps import DocumentReadCtx, FinancialWriteCtx, SettingsDep, require_documents_enabled
from meip.core.errors import FeatureDisabledError
from meip.documents import statement_review
from meip.documents.schemas import BankAccountOut, HolderIn


def require_bank_statements(settings: SettingsDep) -> None:
    if not settings.bank_statements_allowed:
        raise FeatureDisabledError("Bank statements are not enabled in this deployment")


router = APIRouter(
    prefix="/api/v1/cases/{case_id}/bank-accounts",
    tags=["bank accounts"],
    dependencies=[Depends(require_documents_enabled), Depends(require_bank_statements)],
)


@router.get(
    "",
    response_model=list[BankAccountOut],
    summary="List the case's bank accounts",
    description="Each account with its holders (the case's applicants), whether it is joint, "
    "and its confirmed statements.",
)
async def list_accounts(case_id: uuid.UUID, ctx: DocumentReadCtx) -> list[BankAccountOut]:
    return await statement_review.list_accounts(ctx, case_id)


@router.post(
    "/{account_id}/holders",
    response_model=BankAccountOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add a holder to an account",
    description="An applicant on the case who also holds the account (making it joint). "
    "Holders are added, never changed or removed. Audited.",
    responses={
        404: {"description": "Case or account not found"},
        409: {"description": "The applicant already holds the account"},
        422: {"description": "Not an active applicant on the case, or a closed case"},
    },
)
async def add_holder(
    case_id: uuid.UUID,
    account_id: uuid.UUID,
    body: HolderIn,
    ctx: FinancialWriteCtx,
) -> Annotated[BankAccountOut, None]:
    out = await statement_review.add_holder(ctx, case_id, account_id, body)
    await ctx.commit()
    return out
