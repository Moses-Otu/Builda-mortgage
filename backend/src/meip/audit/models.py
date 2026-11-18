"""Append-only audit event log.

Rows are immutable: the runtime role has INSERT/SELECT only, and a trigger rejects UPDATE
and DELETE for every role (including the owner).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from meip.core.db import Base, utcnow
from meip.core.ids import uuid7


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        CheckConstraint("actor_type IN ('user', 'operator', 'system')", name="actor_type"),
        CheckConstraint(
            "(actor_type = 'user' AND actor_user_id IS NOT NULL AND actor_ref IS NULL)"
            " OR (actor_type = 'operator' AND actor_user_id IS NULL AND actor_ref IS NOT NULL)"
            " OR (actor_type = 'system' AND actor_user_id IS NULL)",
            name="actor_consistency",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    occurred_at: Mapped[datetime] = mapped_column(default=utcnow)
    actor_type: Mapped[str] = mapped_column(String(16))
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    # Identifies a non-user actor, e.g. the platform operator who ran an onboarding command.
    actor_ref: Mapped[str | None] = mapped_column(String(255))
    action: Mapped[str] = mapped_column(String(64))
    entity_type: Mapped[str] = mapped_column(String(64))
    entity_id: Mapped[uuid.UUID] = mapped_column()
    # Deliberately no FK: the log must outlive and be independent of the entities it records.
    case_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    request_id: Mapped[str | None] = mapped_column(String(64))
    changes: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
