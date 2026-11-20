"""Case services."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select

from meip.applicants.models import ACTIVE, Applicant, PartyRole
from meip.audit import service as audit
from meip.audit.service import AuditAction
from meip.cases import lifecycle
from meip.cases.models import Case, CaseStatus, ClosureReason
from meip.cases.schemas import CaseCreate, CaseOut, CaseTransition, CaseUpdate, CaseWarning
from meip.core.db import utcnow
from meip.core.errors import (
    BusinessRuleError,
    ConflictError,
    InvalidStateTransitionError,
    NotFoundError,
    VersionConflictError,
)
from meip.core.money import Money
from meip.tenancy.context import OperationContext
from meip.tenancy.models import Role
from meip.tenancy.permissions import ADVISER_ELIGIBLE_ROLES, Permission
from meip.tenancy.service import get_active_membership

# [A] Typical UK residential terms run 5-40 years. Outside that is worth a second look,
# but lender policy varies, so it is a warning (D7). Candidate for firm configuration.
TYPICAL_TERM_MONTHS = range(60, 481)

AUDITED_FIELDS = (
    "purpose",
    "property_use",
    "status",
    "closure_reason",
    "closure_note",
    "external_reference",
    "adviser_membership_id",
    "property_value_minor",
    "loan_amount_minor",
    "term_months",
)


async def _applicant_count(ctx: OperationContext, case_id: uuid.UUID) -> int:
    """Active applicants only: removed applicants remain on record but not on the case."""
    count = await ctx.session.scalar(
        select(func.count()).select_from(Applicant).where(Applicant.case_id == case_id, ACTIVE)
    )
    return int(count or 0)


async def _has_primary_borrower(ctx: OperationContext, case_id: uuid.UUID) -> bool:
    """Defence in depth: applicant services keep an active primary whenever a borrower
    exists; progressing a case also checks for one directly."""
    count = await ctx.session.scalar(
        select(func.count())
        .select_from(Applicant)
        .where(
            Applicant.case_id == case_id,
            ACTIVE,
            Applicant.party_role == PartyRole.BORROWER.value,
            Applicant.is_primary.is_(True),
        )
    )
    return bool(count)


def warnings_for(case: Case) -> list[CaseWarning]:
    warnings: list[CaseWarning] = []
    if (
        case.property_value_minor is not None
        and case.loan_amount_minor is not None
        and case.loan_amount_minor > case.property_value_minor
    ):
        warnings.append(
            CaseWarning(
                code="loan_exceeds_property_value",
                message="The loan amount is greater than the property value",
            )
        )
    if case.term_months is not None and case.term_months not in TYPICAL_TERM_MONTHS:
        warnings.append(
            CaseWarning(
                code="term_outside_typical_range",
                message="The term is outside the typical 5 to 40 year range",
            )
        )
    return warnings


def to_out(case: Case, applicant_count: int) -> CaseOut:
    return CaseOut(
        id=case.id,
        purpose=case.purpose,
        property_use=case.property_use,
        status=case.status,
        closure_reason=case.closure_reason,
        closure_note=case.closure_note,
        external_reference=case.external_reference,
        adviser_membership_id=case.adviser_membership_id,
        property_value=Money.from_optional_minor(case.property_value_minor),
        loan_amount=Money.from_optional_minor(case.loan_amount_minor),
        term_months=case.term_months,
        applicant_count=applicant_count,
        warnings=warnings_for(case),
        created_by_user_id=case.created_by_user_id,
        created_at=case.created_at,
        updated_at=case.updated_at,
        status_changed_at=case.status_changed_at,
        version=case.version,
    )


async def get_case(ctx: OperationContext, case_id: uuid.UUID, *, for_update: bool = False) -> Case:
    stmt = select(Case).where(Case.id == case_id, Case.tenant_id == ctx.tenant_id)
    if for_update:
        stmt = stmt.with_for_update()
    case: Case | None = await ctx.session.scalar(stmt)
    if case is None:
        raise NotFoundError("Case not found")
    return case


async def get_case_out(ctx: OperationContext, case_id: uuid.UUID) -> CaseOut:
    case = await get_case(ctx, case_id)
    return to_out(case, await _applicant_count(ctx, case.id))


async def list_cases(
    ctx: OperationContext,
    *,
    status: CaseStatus | None,
    adviser_membership_id: uuid.UUID | None,
    limit: int,
    offset: int,
) -> tuple[list[CaseOut], int]:
    applicants = (
        select(Applicant.case_id, func.count().label("n"))
        .where(ACTIVE)
        .group_by(Applicant.case_id)
        .subquery()
    )
    stmt = (
        select(Case, func.coalesce(applicants.c.n, 0))
        .outerjoin(applicants, applicants.c.case_id == Case.id)
        .where(Case.tenant_id == ctx.tenant_id)
    )
    if status is not None:
        stmt = stmt.where(Case.status == status.value)
    if adviser_membership_id is not None:
        stmt = stmt.where(Case.adviser_membership_id == adviser_membership_id)

    total = await ctx.session.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = await ctx.session.execute(
        stmt.order_by(Case.created_at.desc(), Case.id.desc()).limit(limit).offset(offset)
    )
    return [to_out(case, int(n)) for case, n in rows], int(total or 0)


async def _validate_adviser(ctx: OperationContext, membership_id: uuid.UUID) -> None:
    ctx.require(Permission.CASE_ASSIGN_ADVISER)
    membership = await get_active_membership(ctx.session, ctx.tenant_id, membership_id)
    if membership is None or Role(membership.role) not in ADVISER_ELIGIBLE_ROLES:
        raise BusinessRuleError("Adviser must be an active adviser or firm administrator")


async def create_case(ctx: OperationContext, body: CaseCreate) -> CaseOut:
    if body.adviser_membership_id is not None:
        await _validate_adviser(ctx, body.adviser_membership_id)
    property_value = body.property_value.amount_minor if body.property_value else None
    loan_amount = body.loan_amount.amount_minor if body.loan_amount else None

    case = Case(
        tenant_id=ctx.tenant_id,
        purpose=body.purpose.value,
        property_use=body.property_use.value if body.property_use else None,
        status=CaseStatus.DRAFT.value,
        external_reference=body.external_reference,
        adviser_membership_id=body.adviser_membership_id,
        property_value_minor=property_value,
        loan_amount_minor=loan_amount,
        term_months=body.term_months,
        created_by_user_id=ctx.actor.user_id,
    )
    ctx.session.add(case)
    await ctx.session.flush()
    await audit.record(
        ctx.session,
        tenant_id=ctx.tenant_id,
        actor=ctx.actor,
        action=AuditAction.CASE_CREATED,
        entity_type="case",
        entity_id=case.id,
        case_id=case.id,
        change_set=audit.creation(audit.snapshot(case, AUDITED_FIELDS)),
    )
    return to_out(case, 0)


def _require_editable(case: Case) -> None:
    if CaseStatus(case.status) not in lifecycle.EDITABLE_STATUSES:
        raise ConflictError(f"A case in status '{case.status}' can no longer be edited")


async def update_case(
    ctx: OperationContext, case_id: uuid.UUID, body: CaseUpdate, expected_version: int
) -> CaseOut:
    case = await get_case(ctx, case_id, for_update=True)
    if case.version != expected_version:
        raise VersionConflictError()
    _require_editable(case)

    provided = body.model_fields_set
    if "purpose" in provided and body.purpose is None:
        raise BusinessRuleError("purpose cannot be cleared")
    if (
        "adviser_membership_id" in provided
        and body.adviser_membership_id != case.adviser_membership_id
    ):
        if body.adviser_membership_id is None:
            ctx.require(Permission.CASE_ASSIGN_ADVISER)
        else:
            await _validate_adviser(ctx, body.adviser_membership_id)

    updates: dict[str, Any] = {}
    if "purpose" in provided and body.purpose is not None:
        updates["purpose"] = body.purpose.value
    if "property_use" in provided:
        updates["property_use"] = body.property_use.value if body.property_use else None
    if "external_reference" in provided:
        updates["external_reference"] = body.external_reference
    if "adviser_membership_id" in provided:
        updates["adviser_membership_id"] = body.adviser_membership_id
    if "property_value" in provided:
        updates["property_value_minor"] = (
            body.property_value.amount_minor if body.property_value else None
        )
    if "loan_amount" in provided:
        updates["loan_amount_minor"] = body.loan_amount.amount_minor if body.loan_amount else None
    if "term_months" in provided:
        updates["term_months"] = body.term_months

    before = audit.snapshot(case, AUDITED_FIELDS)
    for name, value in updates.items():
        setattr(case, name, value)
    changes = audit.diff(before, audit.snapshot(case, AUDITED_FIELDS))
    if not changes:
        raise BusinessRuleError("The update does not change anything")

    await ctx.session.flush()
    await audit.record(
        ctx.session,
        tenant_id=ctx.tenant_id,
        actor=ctx.actor,
        action=AuditAction.CASE_UPDATED,
        entity_type="case",
        entity_id=case.id,
        case_id=case.id,
        change_set=changes,
    )
    return to_out(case, await _applicant_count(ctx, case.id))


async def transition_case(
    ctx: OperationContext, case_id: uuid.UUID, body: CaseTransition, expected_version: int
) -> CaseOut:
    case = await get_case(ctx, case_id, for_update=True)
    if case.version != expected_version:
        raise VersionConflictError()

    current, target = CaseStatus(case.status), body.to_status
    if not lifecycle.is_defined(current, target):
        raise InvalidStateTransitionError(
            f"Cannot move a case from '{current.value}' to '{target.value}'"
        )
    unavailable = lifecycle.unavailable_reason(current, target)
    if unavailable is not None:
        raise InvalidStateTransitionError(unavailable, extra={"available": False})

    closing, reopening = target is CaseStatus.CLOSED, lifecycle.is_reopening(current, target)
    if body.closure_reason is not None and not closing:
        raise BusinessRuleError("closure_reason is only allowed when closing a case")
    if closing or reopening:
        # Closing and reopening are the same authority: whoever may end a case may resume it.
        ctx.require(Permission.CASE_CLOSE)
    if closing:
        if body.closure_reason is None:
            raise BusinessRuleError("A closure_reason is required to close a case")
        if body.closure_reason is ClosureReason.OTHER and not body.note:
            raise BusinessRuleError("A note is required when the closure reason is 'other'")
    if reopening and not body.note:
        raise BusinessRuleError("A note explaining why is required to reopen a case")

    applicant_count = await _applicant_count(ctx, case.id)
    if target in (
        CaseStatus.COLLECTING_EVIDENCE,
        CaseStatus.IN_REVIEW,
    ) and not await _has_primary_borrower(ctx, case.id):
        raise BusinessRuleError("The case needs an active primary borrower first")

    before = audit.snapshot(case, ("status", "closure_reason"))
    case.status = target.value
    case.status_changed_at = utcnow()
    if closing:
        case.closure_reason = body.closure_reason.value if body.closure_reason else None
        case.closure_note = body.note
    elif current is CaseStatus.CLOSED:
        # Reopened: the closure no longer applies. Its record remains in the audit history.
        case.closure_reason = None
        case.closure_note = None
    # Stage 3-B: closing a verified case keeps its final approval; any reopening supersedes
    # it. The pointer is cleared in the same UPDATE as the status (a CHECK requires it); the
    # approval row itself remains as history.
    superseded = case.approval_id if not closing else None
    if superseded is not None:
        case.approval_id = None
    await ctx.session.flush()
    changes = (
        audit.diff(before, audit.snapshot(case, ("status", "closure_reason")))
        .note("note", body.note)
        .note("superseded_approval_id", superseded)
    )
    await audit.record(
        ctx.session,
        tenant_id=ctx.tenant_id,
        actor=ctx.actor,
        action=AuditAction.CASE_STATUS_CHANGED,
        entity_type="case",
        entity_id=case.id,
        case_id=case.id,
        change_set=changes,
    )
    return to_out(case, applicant_count)


def require_editable(case: Case) -> None:
    """Public guard used by sub-resources (applicants) of a case."""
    _require_editable(case)
