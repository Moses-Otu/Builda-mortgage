"""Applicant services. Applicants are a sub-resource of a case."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select

from meip.applicants.models import Applicant
from meip.applicants.schemas import ApplicantCreate, ApplicantOut, ApplicantUpdate
from meip.audit import service as audit
from meip.audit.service import AuditAction
from meip.cases.service import get_case, require_editable
from meip.core.errors import BusinessRuleError, NotFoundError, VersionConflictError
from meip.core.money import Money
from meip.tenancy.context import OperationContext

AUDITED_FIELDS = (
    "position",
    "first_name",
    "middle_names",
    "last_name",
    "date_of_birth",
    "employment_status",
    "declared_gross_annual_income_minor",
    "declared_monthly_commitments_minor",
)

# API field name -> (column name, is_money)
_FIELD_MAP: dict[str, tuple[str, bool]] = {
    "first_name": ("first_name", False),
    "middle_names": ("middle_names", False),
    "last_name": ("last_name", False),
    "date_of_birth": ("date_of_birth", False),
    "employment_status": ("employment_status", False),
    "declared_gross_annual_income": ("declared_gross_annual_income_minor", True),
    "declared_monthly_commitments": ("declared_monthly_commitments_minor", True),
}
_REQUIRED = frozenset({"first_name", "last_name"})


def to_out(a: Applicant) -> ApplicantOut:
    return ApplicantOut(
        id=a.id,
        case_id=a.case_id,
        position=a.position,
        first_name=a.first_name,
        middle_names=a.middle_names,
        last_name=a.last_name,
        date_of_birth=a.date_of_birth,
        employment_status=a.employment_status,
        declared_gross_annual_income=Money.from_optional_minor(
            a.declared_gross_annual_income_minor
        ),
        declared_monthly_commitments=Money.from_optional_minor(
            a.declared_monthly_commitments_minor
        ),
        created_at=a.created_at,
        updated_at=a.updated_at,
        version=a.version,
    )


def _column_value(value: Any, is_money: bool) -> Any:
    if is_money:
        return value.amount_minor if isinstance(value, Money) else None
    return value.value if hasattr(value, "value") else value


async def list_applicants(ctx: OperationContext, case_id: uuid.UUID) -> list[ApplicantOut]:
    await get_case(ctx, case_id)
    rows = await ctx.session.scalars(
        select(Applicant)
        .where(Applicant.case_id == case_id, Applicant.tenant_id == ctx.tenant_id)
        .order_by(Applicant.position)
    )
    return [to_out(a) for a in rows]


async def _get_applicant(
    ctx: OperationContext, case_id: uuid.UUID, applicant_id: uuid.UUID
) -> Applicant:
    applicant: Applicant | None = await ctx.session.scalar(
        select(Applicant).where(
            Applicant.id == applicant_id,
            Applicant.case_id == case_id,
            Applicant.tenant_id == ctx.tenant_id,
        )
    )
    if applicant is None:
        raise NotFoundError("Applicant not found")
    return applicant


async def get_applicant(
    ctx: OperationContext, case_id: uuid.UUID, applicant_id: uuid.UUID
) -> ApplicantOut:
    await get_case(ctx, case_id)
    return to_out(await _get_applicant(ctx, case_id, applicant_id))


async def add_applicant(
    ctx: OperationContext, case_id: uuid.UUID, body: ApplicantCreate, max_applicants: int
) -> ApplicantOut:
    # Lock the case row so concurrent adds cannot claim the same position.
    case = await get_case(ctx, case_id, for_update=True)
    require_editable(case)

    taken = set(
        await ctx.session.scalars(select(Applicant.position).where(Applicant.case_id == case_id))
    )
    if len(taken) >= max_applicants:
        raise BusinessRuleError(f"A case can have at most {max_applicants} applicants")
    position = min(p for p in range(1, max_applicants + 1) if p not in taken)

    values = {
        column: _column_value(getattr(body, field), is_money)
        for field, (column, is_money) in _FIELD_MAP.items()
    }
    applicant = Applicant(tenant_id=ctx.tenant_id, case_id=case_id, position=position, **values)
    ctx.session.add(applicant)
    await ctx.session.flush()
    await audit.record(
        ctx.session,
        tenant_id=ctx.tenant_id,
        actor=ctx.actor,
        action=AuditAction.APPLICANT_ADDED,
        entity_type="applicant",
        entity_id=applicant.id,
        case_id=case_id,
        change_set=audit.creation(audit.snapshot(applicant, AUDITED_FIELDS)),
    )
    return to_out(applicant)


async def update_applicant(
    ctx: OperationContext, case_id: uuid.UUID, applicant_id: uuid.UUID, body: ApplicantUpdate
) -> ApplicantOut:
    # Lock the case row: a concurrent status transition must not slip in between the
    # editability check and this write.
    case = await get_case(ctx, case_id, for_update=True)
    applicant = await _get_applicant(ctx, case_id, applicant_id)
    if applicant.version != body.version:
        raise VersionConflictError()
    require_editable(case)

    provided = body.model_fields_set - {"version"}
    cleared_required = {f for f in provided & _REQUIRED if getattr(body, f) is None}
    if cleared_required:
        raise BusinessRuleError(
            f"Cannot clear required fields: {', '.join(sorted(cleared_required))}"
        )

    before = audit.snapshot(applicant, AUDITED_FIELDS)
    for field in provided:
        column, is_money = _FIELD_MAP[field]
        setattr(applicant, column, _column_value(getattr(body, field), is_money))
    changes = audit.diff(before, audit.snapshot(applicant, AUDITED_FIELDS))
    if not changes:
        raise BusinessRuleError("The update does not change anything")

    await ctx.session.flush()
    await audit.record(
        ctx.session,
        tenant_id=ctx.tenant_id,
        actor=ctx.actor,
        action=AuditAction.APPLICANT_UPDATED,
        entity_type="applicant",
        entity_id=applicant.id,
        case_id=case_id,
        change_set=changes,
    )
    return to_out(applicant)


async def remove_applicant(
    ctx: OperationContext, case_id: uuid.UUID, applicant_id: uuid.UUID, version: int
) -> None:
    case = await get_case(ctx, case_id, for_update=True)
    applicant = await _get_applicant(ctx, case_id, applicant_id)
    if applicant.version != version:
        raise VersionConflictError()
    require_editable(case)

    position = applicant.position
    await ctx.session.delete(applicant)
    await ctx.session.flush()
    await audit.record(
        ctx.session,
        tenant_id=ctx.tenant_id,
        actor=ctx.actor,
        action=AuditAction.APPLICANT_REMOVED,
        entity_type="applicant",
        entity_id=applicant_id,
        case_id=case_id,
        change_set=audit.diff({"position": position}, {}),
    )
