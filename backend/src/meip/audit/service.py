"""Audit recording.

Every mutation writes exactly one event in the same transaction as the change, so the
change and its audit record commit or roll back together.

Each change is classified so that the permanent, append-only ``audit_events`` row never
holds personal data:

* ``REDACTED_FIELDS`` (direct identifiers such as names and dates of birth) are recorded
  only as ``{"changed": true}``. Their values are stored nowhere in the audit trail.
* ``NON_PERSONAL_FIELDS`` (statuses, roles, codes, internal IDs) are recorded in full in
  ``audit_events.changes``.
* **Every other field** (amounts, terms, free-text reasons and notes, references) is marked
  ``changed`` in ``audit_events.changes``, with its values held in ``audit_event_details``.
  That table can be erased under the retention policy without touching the immutable log.
  Unknown fields default to this erasable class, so a new field fails safe.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from meip.audit.models import AuditEvent, AuditEventDetail
from meip.core.request_context import request_id_var
from meip.tenancy.context import Actor, ActorKind

REDACTED_FIELDS = frozenset(
    {
        "first_name",
        "middle_names",
        "last_name",
        "date_of_birth",
        "email",
        "display_name",
        "idp_subject",
    }
)

NON_PERSONAL_FIELDS = frozenset(
    {
        # lifecycle and classification codes
        "status",
        "role",
        "purpose",
        "property_use",
        "closure_reason",
        "removal_reason",
        "party_role",
        "on_title",
        "is_primary",
        "display_order",
        "kind",
        "basis",
        "category",
        "scope",
        "measure",
        "value_type",
        "temporal_kind",
        "source_kind",
        "frequency",
        "liability_role",
        # internal identifiers and firm data
        "user_id",
        "applicant_id",
        "adviser_membership_id",
        "admin_membership_id",
        "new_primary_applicant_id",
        "previous_primary_applicant_id",
        "income_source_id",
        "commitment_id",
        "expenditure_item_id",
        "supersedes_observation_id",
        "retired_reason",
        "withdrawal_reason",
        "retired_observation_ids",
        "currency",
        "to_be_repaid_before_completion",
        # No "name": it means a firm's name in one place and an employer or business in
        # another. Unclassified fields are erasable, which is the safe default.
        "fca_frn",
        "max_applicants",
        "require_four_eyes_verification",
        # documents (file names and content hashes are erasable, so not listed)
        "document_type",
        "document_version_id",
        "size_bytes",
        "media_type",
        "page_count",
        "failure_code",
        # extraction: versions, marker names and counts only (never labels or values)
        "classification",
        "schema_version",
        "markers",
        "field_count",
        "payment_line_count",
        "deduction_line_count",
    }
)


class AuditAction(StrEnum):
    TENANT_CREATED = "tenant.created"
    FIRM_SETTINGS_UPDATED = "firm_settings.updated"
    MEMBER_ADDED = "member.added"
    MEMBER_UPDATED = "member.updated"
    # Written by the app_accept_invitation() database function, not by Python code.
    MEMBER_INVITATION_ACCEPTED = "member.invitation_accepted"
    CASE_CREATED = "case.created"
    CASE_UPDATED = "case.updated"
    CASE_STATUS_CHANGED = "case.status_changed"
    APPLICANT_ADDED = "applicant.added"
    APPLICANT_UPDATED = "applicant.updated"
    APPLICANT_REMOVED = "applicant.removed"
    APPLICANT_REINSTATED = "applicant.reinstated"
    FINANCIAL_ITEM_ADDED = "financial_item.added"
    FINANCIAL_ITEM_UPDATED = "financial_item.updated"
    FINANCIAL_ITEM_WITHDRAWN = "financial_item.withdrawn"
    OBSERVATION_RECORDED = "observation.recorded"
    OBSERVATION_RETIRED = "observation.retired"
    DOCUMENT_UPLOADED = "document.uploaded"
    DOCUMENT_PROCESSED = "document.processed"
    DOCUMENT_PROCESSING_FAILED = "document.processing_failed"
    DOCUMENT_EXTRACTED = "document.extracted"
    DOCUMENT_UNRECOGNISED = "document.unrecognised"


@dataclass
class ChangeSet:
    """An audit change, split into its permanent and its erasable parts."""

    changes: dict[str, Any] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.changes)

    def note(self, name: str, value: Any) -> ChangeSet:
        """Attach a non-diff value (e.g. a reason), classified like any other field."""
        if value is None:
            return self
        if name in REDACTED_FIELDS:
            self.changes[name] = {"changed": True}
        elif name in NON_PERSONAL_FIELDS:
            self.changes[name] = _jsonable(value)
        else:
            self.changes[name] = {"changed": True}
            self.details[name] = _jsonable(value)
        return self


def _jsonable(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, date | datetime):
        return value.isoformat()
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, Decimal):
        return str(value)  # exact; floats would lose precision
    return value


def snapshot(entity: object, fields: tuple[str, ...]) -> dict[str, Any]:
    return {name: getattr(entity, name) for name in fields}


def diff(before: dict[str, Any], after: dict[str, Any]) -> ChangeSet:
    """Field-level change set, classified into permanent and erasable parts."""
    result = ChangeSet()
    for name in sorted(set(before) | set(after)):
        old, new = before.get(name), after.get(name)
        if old == new:
            continue
        transition = {"from": _jsonable(old), "to": _jsonable(new)}
        if name in REDACTED_FIELDS:
            result.changes[name] = {"changed": True}
        elif name in NON_PERSONAL_FIELDS:
            result.changes[name] = transition
        else:
            result.changes[name] = {"changed": True}
            result.details[name] = transition
    return result


def creation(after: dict[str, Any]) -> ChangeSet:
    return diff({}, after)


async def record(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    actor: Actor,
    action: AuditAction,
    entity_type: str,
    entity_id: uuid.UUID,
    case_id: uuid.UUID | None = None,
    change_set: ChangeSet | None = None,
    correlation_id: str | None = None,
) -> AuditEvent:
    change_set = change_set or ChangeSet()
    event = AuditEvent(
        tenant_id=tenant_id,
        actor_type=actor.kind.value,
        actor_user_id=actor.user_id if actor.kind is ActorKind.USER else None,
        actor_ref=None if actor.kind is ActorKind.USER else actor.ref,
        on_behalf_of_user_id=actor.on_behalf_of_user_id,
        action=action.value,
        entity_type=entity_type,
        entity_id=entity_id,
        case_id=case_id,
        request_id=request_id_var.get(),
        correlation_id=correlation_id,
        changes=change_set.changes,
    )
    session.add(event)
    await session.flush()
    if change_set.details:
        session.add(
            AuditEventDetail(event_id=event.id, tenant_id=tenant_id, details=change_set.details)
        )
        await session.flush()
    return event


async def list_for_case(
    session: AsyncSession, case_id: uuid.UUID, *, limit: int, offset: int
) -> tuple[list[tuple[AuditEvent, dict[str, Any] | None]], int]:
    """Events oldest first, each with its erasable details (``None`` if none or erased)."""
    total = await session.scalar(
        select(func.count()).select_from(AuditEvent).where(AuditEvent.case_id == case_id)
    )
    rows = await session.execute(
        select(AuditEvent, AuditEventDetail.details)
        .outerjoin(AuditEventDetail, AuditEventDetail.event_id == AuditEvent.id)
        .where(AuditEvent.case_id == case_id)
        .order_by(AuditEvent.occurred_at, AuditEvent.id)
        .limit(limit)
        .offset(offset)
    )
    return [(event, details) for event, details in rows], int(total or 0)
