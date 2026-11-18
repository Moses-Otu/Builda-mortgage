"""Append-only audit event log.

``audit_events`` rows are immutable: the runtime role has INSERT/SELECT only, and a trigger
rejects UPDATE and DELETE for every role (including the owner). They hold no personal data.

``audit_event_details`` holds the erasable values of an event (amounts, reasons, notes). It
is append-only for the runtime role; only the owner-run retention process may delete rows.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, ForeignKeyConstraint, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from meip.core.db import Base, utcnow
from meip.core.ids import uuid7

# A user acts for themselves; an operator names themselves; system and integration actors
# may record the user whose action caused them (on_behalf_of_user_id).
ACTOR_CONSISTENCY = (
    "(actor_type = 'user' AND actor_user_id IS NOT NULL AND actor_ref IS NULL"
    " AND on_behalf_of_user_id IS NULL)"
    " OR (actor_type = 'operator' AND actor_user_id IS NULL AND actor_ref IS NOT NULL"
    " AND on_behalf_of_user_id IS NULL)"
    " OR (actor_type = 'system' AND actor_user_id IS NULL)"
    " OR (actor_type = 'integration' AND actor_user_id IS NULL AND actor_ref IS NOT NULL)"
)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        CheckConstraint(
            "actor_type IN ('user', 'operator', 'system', 'integration')", name="actor_type"
        ),
        CheckConstraint(ACTOR_CONSISTENCY, name="actor_consistency"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    occurred_at: Mapped[datetime] = mapped_column(default=utcnow)
    actor_type: Mapped[str] = mapped_column(String(16))
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    # Identifies a non-user actor, e.g. the platform operator who ran an onboarding command.
    actor_ref: Mapped[str | None] = mapped_column(String(255))
    on_behalf_of_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(64))
    entity_type: Mapped[str] = mapped_column(String(64))
    entity_id: Mapped[uuid.UUID] = mapped_column()
    # Deliberately no FK: the log must outlive and be independent of the entities it records.
    case_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    request_id: Mapped[str | None] = mapped_column(String(64))
    # Job ID for background work; HTTP requests are correlated by request_id.
    correlation_id: Mapped[str | None] = mapped_column(String(64))
    changes: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class AuditEventDetail(Base):
    """Erasable values belonging to one audit event."""

    __tablename__ = "audit_event_details"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "event_id"],
            ["audit_events.tenant_id", "audit_events.id"],
            name="fk_audit_event_details_event",
        ),
    )

    event_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB)
