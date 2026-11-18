"""Audit recording.

Every mutation writes exactly one event in the same transaction as the change, so the
change and its audit record commit or roll back together.

Direct identifiers are not copied into audit diffs (only that they changed): the audit log
must be retained long-term, and keeping names/DOBs out of it means erasure of a borrower's
personal data does not require rewriting an append-only log. Financial and status values
are kept, because they are what a reviewer needs to reconstruct a decision trail.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from meip.audit.models import AuditEvent
from meip.core.request_context import request_id_var

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


class AuditAction(StrEnum):
    TENANT_CREATED = "tenant.created"
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


def _jsonable(value: Any) -> Any:
    if isinstance(value, uuid.UUID | date | datetime):
        return value.isoformat() if not isinstance(value, uuid.UUID) else str(value)
    return value


def snapshot(entity: object, fields: tuple[str, ...]) -> dict[str, Any]:
    return {name: getattr(entity, name) for name in fields}


def diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Field-level change set. Redacted fields record only that a change occurred."""
    changes: dict[str, Any] = {}
    for name in sorted(set(before) | set(after)):
        old, new = before.get(name), after.get(name)
        if old == new:
            continue
        if name in REDACTED_FIELDS:
            changes[name] = {"changed": True}
        else:
            changes[name] = {"from": _jsonable(old), "to": _jsonable(new)}
    return changes


def creation(after: dict[str, Any]) -> dict[str, Any]:
    return diff({}, after)


async def record(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    actor_user_id: uuid.UUID | None,
    action: AuditAction,
    actor_ref: str | None = None,
    entity_type: str,
    entity_id: uuid.UUID,
    case_id: uuid.UUID | None = None,
    changes: dict[str, Any] | None = None,
) -> AuditEvent:
    event = AuditEvent(
        tenant_id=tenant_id,
        actor_type="user" if actor_user_id else ("operator" if actor_ref else "system"),
        actor_user_id=actor_user_id,
        actor_ref=None if actor_user_id else actor_ref,
        action=action.value,
        entity_type=entity_type,
        entity_id=entity_id,
        case_id=case_id,
        request_id=request_id_var.get(),
        changes=changes or {},
    )
    session.add(event)
    await session.flush()
    return event


async def list_for_case(
    session: AsyncSession, case_id: uuid.UUID, *, limit: int, offset: int
) -> tuple[list[AuditEvent], int]:
    base = select(AuditEvent).where(AuditEvent.case_id == case_id)
    total = await session.scalar(select(func.count()).select_from(base.subquery()))
    rows = await session.scalars(
        base.order_by(AuditEvent.occurred_at, AuditEvent.id).limit(limit).offset(offset)
    )
    return list(rows), int(total or 0)
