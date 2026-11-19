"""The only reconciliation data stored: a broker's acknowledgement of a discrepancy.

Findings themselves are computed on request and never stored. An acknowledgement records that
a broker has seen a discrepancy, with a note; it does not resolve the discrepancy, approve any
figure or change the financial profile. Insert-only for the runtime role (SELECT and INSERT
grants only), like document reviews; the owner role can still erase rows under the retention
policy, because the note is free text a broker wrote."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, ForeignKeyConstraint, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from meip.core.db import Base, utcnow
from meip.core.ids import uuid7

NOTE_MAX = 1000


class CheckAcknowledgement(Base):
    __tablename__ = "check_acknowledgements"
    __table_args__ = (
        UniqueConstraint("tenant_id", "case_id", "finding_key"),
        ForeignKeyConstraint(
            ["tenant_id", "case_id"],
            ["cases.tenant_id", "cases.id"],
            name="fk_check_acknowledgements_case",
        ),
        CheckConstraint("finding_key ~ '^[0-9a-f]{64}$'", name="finding_key"),
        CheckConstraint(f"length(trim(note)) BETWEEN 1 AND {NOTE_MAX}", name="note_length"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    case_id: Mapped[uuid.UUID] = mapped_column()
    finding_key: Mapped[str] = mapped_column(String(64))
    rule_code: Mapped[str] = mapped_column(String(32))
    ruleset_version: Mapped[str] = mapped_column(String(32))
    note: Mapped[str] = mapped_column(String(NOTE_MAX))
    acknowledged_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
