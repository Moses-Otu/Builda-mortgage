"""Case services."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select

from meip.applicants.models import Applicant
from meip.audit import service as audit
from meip.audit.service import AuditAction
from meip.cases import lifecycle
from meip.cases.models import Case, CaseStatus
from meip.cases.schemas import CaseCreate, CaseOut, CaseTransition, CaseUpdate
from meip.core.db import utcnow
from meip.core.errors import (
    BusinessRuleError,
    ConflictError,
    InvalidStateTransitionError,
    NotFoundError,
    VersionConflictError,
)
from meip.core.money import Money
from meip.tenancy.context import RequestContext
from meip.tenancy.models import Role
from meip.tenancy.permissions import ADVISER_ELIGIBLE_ROLES, Permission
from meip.tenancy.service import get_active_membership

AUDITED_FIELDS = (
    "purpose",
    "status",
    "external_reference",
    "adviser_membership_id",
    "property_value_minor",
    "loan_amount_minor",
    "term_months",
)


async def _applicant_count(ctx: RequestContext, case_id: uuid.UUID) -> int:
    count = await ctx.session.scalar(
        select(func.count()).select_from(Applicant).where(Applicant.case_id == case_id)
    )
    return int(count or 0)


def to_out(case: Case, applicant_count: int) -> CaseOut:
    return CaseOut(
        id=case.id,
        purpose=case.purpose,
        status=case.status,
        external_reference=case.external_reference,
        adviser_membership_id=case.adviser_membership_id,
        property_value=Money.from_optional_minor(case.property_value_minor),
        loan_amount=Money.from_optional_minor(case.loan_amount_minor),
        term_months=case.term_months,
        applicant_count=applicant_count,
        created_by_user_id=case.created_by_user_id,
        created_at=case.created_at,
        updated_at=case.updated_at,
        status_changed_at=case.status_changed_at,
        version=case.version,
    )


async def get_case(ctx: RequestContext, case_id: uuid.UUID, *, for_update: bool = False) -> Case:
    stmt = select(Case).where(Case.id == case_id, Case.tenant_id == ctx.tenant_id)
    if for_update:
        stmt = stmt.with_for_update()
    case: Case | None = await ctx.session.scalar(stmt)
    if case is None:
        raise NotFoundError("Case not found")
    return case


async def get_case_out(ctx: RequestContext, case_id: uuid.UUID) -> CaseOut:
    case = await get_case(ctx, case_id)
    return to_out(case, await _applicant_count(ctx, case.id))


async def list_cases(
    ctx: RequestContext,
    *,
    status: CaseStatus | None,
    adviser_membership_id: uuid.UUID | None,
    limit: int,
    offset: int,
) -> tuple[list[CaseOut], int]:
    applicants = (
        select(Applicant.case_id, func.count().label("n")).group_by(Applicant.case_id).subquery()
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


async def _validate_adviser(ctx: RequestContext, membership_id: uuid.UUID) -> None:
    ctx.require(Permission.CASE_ASSIGN_ADVISER)
    membership = await get_active_membership(ctx.session, ctx.tenant_id, membership_id)
    if membership is None or Role(membership.role) not in ADVISER_ELIGIBLE_ROLES:
        raise BusinessRuleError("Adviser must be an active adviser or firm administrator")


def _validate_amounts(property_value_minor: int | None, loan_amount_minor: int | None) -> None:
    if (
        property_value_minor is not None
        and loan_amount_minor is not None
        and loan_amount_minor > property_value_minor
    ):
        raise BusinessRuleError("Loan amount cannot exceed the property value")


async def create_case(ctx: RequestContext, body: CaseCreate) -> CaseOut:
    if body.adviser_membership_id is not None:
        await _validate_adviser(ctx, body.adviser_membership_id)
    property_value = body.property_value.amount_minor if body.property_value else None
    loan_amount = body.loan_amount.amount_minor if body.loan_amount else None
    _validate_amounts(property_value, loan_amount)

    case = Case(
        tenant_id=ctx.tenant_id,
        purpose=body.purpose.value,
        status=CaseStatus.DRAFT.value,
        external_reference=body.external_reference,
        adviser_membership_id=body.adviser_membership_id,
        property_value_minor=property_value,
        loan_amount_minor=loan_amount,
        term_months=body.term_months,
        created_by_user_id=ctx.user_id,
    )
    ctx.session.add(case)
    await ctx.session.flush()
    await audit.record(
        ctx.session,
        tenant_id=ctx.tenant_id,
        actor_user_id=ctx.user_id,
        action=AuditAction.CASE_CREATED,
        entity_type="case",
        entity_id=case.id,
        case_id=case.id,
        changes=audit.creation(audit.snapshot(case, AUDITED_FIELDS)),
    )
    return to_out(case, 0)


def _require_editable(case: Case) -> None:
    if CaseStatus(case.status) not in lifecycle.EDITABLE_STATUSES:
        raise ConflictError(f"A case in status '{case.status}' can no longer be edited")


async def update_case(ctx: RequestContext, case_id: uuid.UUID, body: CaseUpdate) -> CaseOut:
    case = await get_case(ctx, case_id, for_update=True)
    if case.version != body.version:
        raise VersionConflictError()
    _require_editable(case)

    provided = body.model_fields_set - {"version"}
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

    _validate_amounts(
        updates.get("property_value_minor", case.property_value_minor),
        updates.get("loan_amount_minor", case.loan_amount_minor),
    )

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
        actor_user_id=ctx.user_id,
        action=AuditAction.CASE_UPDATED,
        entity_type="case",
        entity_id=case.id,
        case_id=case.id,
        changes=changes,
    )
    return to_out(case, await _applicant_count(ctx, case.id))


async def transition_case(ctx: RequestContext, case_id: uuid.UUID, body: CaseTransition) -> CaseOut:
    case = await get_case(ctx, case_id, for_update=True)
    if case.version != body.version:
        raise VersionConflictError()

    current, target = CaseStatus(case.status), body.to_status
    if not lifecycle.is_defined(current, target):
        raise InvalidStateTransitionError(
            f"Cannot move a case from '{current.value}' to '{target.value}'"
        )
    reason = lifecycle.unavailable_reason(current, target)
    if reason is not None:
        raise InvalidStateTransitionError(reason, extra={"available": False})

    applicant_count = await _applicant_count(ctx, case.id)
    if target is CaseStatus.CLOSED:
        ctx.require(Permission.CASE_CLOSE)
        if not body.reason:
            raise BusinessRuleError("A reason is required to close a case")
    if target is CaseStatus.COLLECTING_EVIDENCE and applicant_count == 0:
        raise BusinessRuleError("Add at least one applicant before collecting evidence")

    case.status = target.value
    case.status_changed_at = utcnow()
    await ctx.session.flush()
    changes: dict[str, Any] = {"status": {"from": current.value, "to": target.value}}
    if body.reason:
        changes["reason"] = body.reason
    await audit.record(
        ctx.session,
        tenant_id=ctx.tenant_id,
        actor_user_id=ctx.user_id,
        action=AuditAction.CASE_STATUS_CHANGED,
        entity_type="case",
        entity_id=case.id,
        case_id=case.id,
        changes=changes,
    )
    return to_out(case, applicant_count)


def require_editable(case: Case) -> None:
    """Public guard used by sub-resources (applicants) of a case."""
    _require_editable(case)
