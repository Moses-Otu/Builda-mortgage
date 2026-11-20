"""The broker's approval of the financial profile (Stage 3-B).

One row per approval, versioned per case. The snapshot is the exact canonical text of the
profile the broker saw (its SHA-256 is the fingerprint they approved); the attestation is the
exact wording they accepted. Insert-only for the runtime role (SELECT and INSERT grants only):
an approval is never changed. Reopening a case supersedes its approval by clearing
``cases.approval_id``; the row remains as history. Only the owner role, through the retention
process, can remove a row.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from meip.core.db import Base, utcnow
from meip.core.ids import uuid7

NOTE_MAX = 1000


class ProfileApproval(Base):
    __tablename__ = "profile_approvals"
    __table_args__ = (
        UniqueConstraint("tenant_id", "case_id", "id"),
        UniqueConstraint("tenant_id", "case_id", "version"),
        ForeignKeyConstraint(
            ["tenant_id", "case_id"],
            ["cases.tenant_id", "cases.id"],
            name="fk_profile_approvals_case",
        ),
        CheckConstraint("version >= 1", name="version"),
        CheckConstraint("snapshot_sha256 ~ '^[0-9a-f]{64}$'", name="snapshot_sha256"),
        CheckConstraint("length(attestation_text) > 0", name="attestation_text"),
        CheckConstraint(
            f"note IS NULL OR length(trim(note)) BETWEEN 1 AND {NOTE_MAX}", name="note_length"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    case_id: Mapped[uuid.UUID] = mapped_column()
    version: Mapped[int] = mapped_column(Integer)
    profile_rules: Mapped[str] = mapped_column(String(32))
    snapshot: Mapped[str] = mapped_column(Text)
    snapshot_sha256: Mapped[str] = mapped_column(String(64))
    attestation_version: Mapped[str] = mapped_column(String(32))
    attestation_text: Mapped[str] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(String(NOTE_MAX))
    approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    approved_at: Mapped[datetime] = mapped_column(default=utcnow)
