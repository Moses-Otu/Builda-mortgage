"""Applicant services. Applicants are a sub-resource of a case.

Removal never deletes: the applicant row, and everything that references it (documents,
financial records, audit history), stays in place and traceable. Nothing the removed
applicant was party to is reassigned. Reinstatement brings the same applicant (same ID)
back, without reviving anything that was superseded or invalidated while they were removed.

Every operation locks the case row first, so it is serialised against status transitions
and against other applicant changes on the same case (positions, primary flag, limits).
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select

from meip.applicants.models import (
    ACTIVE,
    Applicant,
    ApplicantStatus,
    PartyRole,
    RemovalReason,
)
from meip.applicants.schemas import (
    ApplicantCreate,
    ApplicantOut,
    ApplicantReinstatement,
    ApplicantRemoval,
    ApplicantUpdate,
)
from meip.audit import service as audit
from meip.audit.service import AuditAction
from meip.cases.models import Case
from meip.cases.service import get_case, require_editable
from meip.core.db import utcnow
from meip.core.errors import (
    BusinessRuleError,
    ConflictError,
    NotFoundError,
    VersionConflictError,
)
from meip.tenancy.context import OperationContext

MAX_DISPLAY_ORDER = 8

AUDITED_FIELDS = (
    "display_order",
    "party_role",
    "on_title",
    "is_primary",
    "status",
    "removal_reason",
    "first_name",
    "middle_names",
    "last_name",
    "date_of_birth",
)

# Fields an API caller may set; the API name equals the column name.
_EDITABLE_FIELDS = (
    "party_role",
    "on_title",
    "first_name",
    "middle_names",
    "last_name",
    "date_of_birth",
)
_REQUIRED = frozenset({"first_name", "last_name", "party_role"})


def to_out(a: Applicant) -> ApplicantOut:
    return ApplicantOut(
        id=a.id,
        case_id=a.case_id,
        display_order=a.display_order,
        party_role=a.party_role,
        on_title=a.on_title,
        is_primary=a.is_primary,
        status=a.status,
        removed_at=a.removed_at,
        removal_reason=a.removal_reason,
        removal_note=a.removal_note,
        first_name=a.first_name,
        middle_names=a.middle_names,
        last_name=a.last_name,
        date_of_birth=a.date_of_birth,
        created_at=a.created_at,
        updated_at=a.updated_at,
        version=a.version,
    )


def _column_value(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value


async def list_applicants(
    ctx: OperationContext, case_id: uuid.UUID, *, include_removed: bool = False
) -> list[ApplicantOut]:
    await get_case(ctx, case_id)
    stmt = select(Applicant).where(
        Applicant.case_id == case_id, Applicant.tenant_id == ctx.tenant_id
    )
    if not include_removed:
        stmt = stmt.where(ACTIVE)
    rows = await ctx.session.scalars(
        stmt.order_by(Applicant.status, Applicant.display_order, Applicant.created_at)
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


async def _active_applicants(ctx: OperationContext, case_id: uuid.UUID) -> list[Applicant]:
    rows = await ctx.session.scalars(select(Applicant).where(Applicant.case_id == case_id, ACTIVE))
    return list(rows)


def _free_display_order(active: list[Applicant], preferred: int | None = None) -> int:
    taken = {a.display_order for a in active}
    if preferred is not None and preferred not in taken:
        return preferred
    return min(n for n in range(1, MAX_DISPLAY_ORDER + 1) if n not in taken)


def _primary(active: list[Applicant]) -> Applicant | None:
    return next((a for a in active if a.is_primary), None)


async def _locked_editable_case(ctx: OperationContext, case_id: uuid.UUID) -> Case:
    case = await get_case(ctx, case_id, for_update=True)
    require_editable(case)
    return case


def _check_capacity(active: list[Applicant], max_applicants: int) -> None:
    if len(active) >= max_applicants:
        raise BusinessRuleError(f"A case can have at most {max_applicants} active applicants")


async def _record(
    ctx: OperationContext,
    action: AuditAction,
    applicant: Applicant,
    change_set: audit.ChangeSet,
) -> None:
    await audit.record(
        ctx.session,
        tenant_id=ctx.tenant_id,
        actor=ctx.actor,
        action=action,
        entity_type="applicant",
        entity_id=applicant.id,
        case_id=applicant.case_id,
        change_set=change_set,
    )


async def add_applicant(
    ctx: OperationContext, case_id: uuid.UUID, body: ApplicantCreate, max_applicants: int
) -> ApplicantOut:
    await _locked_editable_case(ctx, case_id)
    active = await _active_applicants(ctx, case_id)
    _check_capacity(active, max_applicants)

    values = {field: _column_value(getattr(body, field)) for field in _EDITABLE_FIELDS}
    applicant = Applicant(
        tenant_id=ctx.tenant_id,
        case_id=case_id,
        display_order=_free_display_order(active),
        is_primary=body.party_role is PartyRole.BORROWER and _primary(active) is None,
        status=ApplicantStatus.ACTIVE.value,
        **values,
    )
    ctx.session.add(applicant)
    await ctx.session.flush()
    await _record(
        ctx,
        AuditAction.APPLICANT_ADDED,
        applicant,
        audit.creation(audit.snapshot(applicant, AUDITED_FIELDS)),
    )
    return to_out(applicant)


async def update_applicant(
    ctx: OperationContext,
    case_id: uuid.UUID,
    applicant_id: uuid.UUID,
    body: ApplicantUpdate,
    expected_version: int,
) -> ApplicantOut:
    # The case lock serialises this against status transitions and other applicant changes.
    await _locked_editable_case(ctx, case_id)
    applicant = await _get_applicant(ctx, case_id, applicant_id)
    if applicant.version != expected_version:
        raise VersionConflictError()
    if applicant.status != ApplicantStatus.ACTIVE.value:
        raise ConflictError("A removed applicant cannot be edited; reinstate them first")

    provided = body.model_fields_set
    cleared_required = {f for f in provided & _REQUIRED if getattr(body, f) is None}
    if cleared_required:
        names = ", ".join(sorted(cleared_required))
        raise BusinessRuleError(f"Cannot clear required fields: {names}")
    if "is_primary" in provided and body.is_primary is None:
        raise BusinessRuleError("is_primary can only be set to true, which moves the flag")

    new_role = body.party_role if "party_role" in provided else PartyRole(applicant.party_role)
    becomes_primary = body.is_primary is True and not applicant.is_primary
    if new_role is PartyRole.GUARANTOR and (applicant.is_primary or becomes_primary):
        raise BusinessRuleError(
            "The primary applicant must be a borrower; make another borrower primary first"
        )

    before = audit.snapshot(applicant, AUDITED_FIELDS)
    for field in provided - {"is_primary"}:
        setattr(applicant, field, _column_value(getattr(body, field)))

    previous_primary: Applicant | None = None
    if becomes_primary:
        previous_primary = _primary(await _active_applicants(ctx, case_id))
        if previous_primary is not None:
            # Demote first: the partial unique index allows one active primary at a time.
            previous_primary.is_primary = False
            await ctx.session.flush()
        applicant.is_primary = True
    elif (
        new_role is PartyRole.BORROWER
        and not applicant.is_primary
        and _primary(await _active_applicants(ctx, case_id)) is None
    ):
        # Invariant: whenever a case has an active borrower, exactly one is primary. A
        # guarantor becoming the only borrower therefore takes the primary flag.
        applicant.is_primary = True

    changes = audit.diff(before, audit.snapshot(applicant, AUDITED_FIELDS))
    if not changes:
        raise BusinessRuleError("The update does not change anything")
    if previous_primary is not None:
        changes.note("previous_primary_applicant_id", previous_primary.id)

    await ctx.session.flush()
    await _record(ctx, AuditAction.APPLICANT_UPDATED, applicant, changes)
    return to_out(applicant)


async def remove_applicant(
    ctx: OperationContext,
    case_id: uuid.UUID,
    applicant_id: uuid.UUID,
    body: ApplicantRemoval,
    expected_version: int,
) -> ApplicantOut:
    """Mark an applicant removed. Their records stay in place and are not reassigned."""
    await _locked_editable_case(ctx, case_id)
    applicant = await _get_applicant(ctx, case_id, applicant_id)
    if applicant.version != expected_version:
        raise VersionConflictError()
    if applicant.status == ApplicantStatus.REMOVED.value:
        raise ConflictError("The applicant has already been removed")
    if body.removal_reason is RemovalReason.OTHER and not body.note:
        raise BusinessRuleError("A note is required when the removal reason is 'other'")

    active = await _active_applicants(ctx, case_id)
    other_borrowers = {
        a.id: a for a in active if a.id != applicant.id and a.party_role == PartyRole.BORROWER.value
    }
    new_primary: Applicant | None = None
    if applicant.is_primary and other_borrowers:
        if body.new_primary_applicant_id not in other_borrowers:
            raise BusinessRuleError(
                "Removing the primary applicant: choose new_primary_applicant_id from the "
                "case's other active borrowers"
            )
        new_primary = other_borrowers[body.new_primary_applicant_id]
    elif body.new_primary_applicant_id is not None:
        raise BusinessRuleError(
            "new_primary_applicant_id only applies when removing the primary applicant "
            "and another active borrower remains"
        )

    before = audit.snapshot(applicant, AUDITED_FIELDS)
    applicant.status = ApplicantStatus.REMOVED.value
    applicant.removed_at = utcnow()
    applicant.removed_by_user_id = ctx.actor.user_id
    applicant.removal_reason = body.removal_reason.value
    applicant.removal_note = body.note
    applicant.is_primary = False
    await ctx.session.flush()
    if new_primary is not None:
        new_primary.is_primary = True
        await ctx.session.flush()

    changes = audit.diff(before, audit.snapshot(applicant, AUDITED_FIELDS)).note("note", body.note)
    if new_primary is not None:
        changes.note("new_primary_applicant_id", new_primary.id)
    await _record(ctx, AuditAction.APPLICANT_REMOVED, applicant, changes)
    return to_out(applicant)


async def reinstate_applicant(
    ctx: OperationContext,
    case_id: uuid.UUID,
    applicant_id: uuid.UUID,
    body: ApplicantReinstatement,
    expected_version: int,
    max_applicants: int,
) -> ApplicantOut:
    """Return a removed applicant (same ID) to the case.

    Only presence on the case is restored. Primary status is not restored if another
    borrower now holds it, and nothing that was invalidated or superseded while the
    applicant was removed (later: broker decisions, approvals) is revived.
    """
    await _locked_editable_case(ctx, case_id)
    applicant = await _get_applicant(ctx, case_id, applicant_id)
    if applicant.version != expected_version:
        raise VersionConflictError()
    if applicant.status != ApplicantStatus.REMOVED.value:
        raise ConflictError("Only a removed applicant can be reinstated")

    active = await _active_applicants(ctx, case_id)
    _check_capacity(active, max_applicants)

    before = audit.snapshot(applicant, AUDITED_FIELDS)
    applicant.status = ApplicantStatus.ACTIVE.value
    applicant.removed_at = None
    applicant.removed_by_user_id = None
    applicant.removal_reason = None
    applicant.removal_note = None
    applicant.display_order = _free_display_order(active, preferred=applicant.display_order)
    applicant.is_primary = (
        applicant.party_role == PartyRole.BORROWER.value and _primary(active) is None
    )
    await ctx.session.flush()
    changes = audit.diff(before, audit.snapshot(applicant, AUDITED_FIELDS)).note("note", body.note)
    await _record(ctx, AuditAction.APPLICANT_REINSTATED, applicant, changes)
    return to_out(applicant)
