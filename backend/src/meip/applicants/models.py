"""Applicants on a mortgage case.

Applicants are owned by a single case (no cross-case borrower records in the MVP), which
keeps personal data scoped to the case it was collected for.

Identity is the applicant UUID, which is never reused. ``display_order`` is presentation
only. Applicants are never deleted: removal sets ``status = 'removed'`` and keeps the row,
so documents, financial records and audit history that reference it stay traceable.

Liability and ownership are separate: ``party_role`` says whether the applicant is liable as
a borrower or guarantees the loan; ``on_title`` says whether they will own the property
(``NULL`` = not yet known). Not every applicant is assumed to be an owner.

What an applicant declares about their finances lives in the financial module (income
sources, commitments, expenditure and their observations), not on this table.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    SmallInteger,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from meip.core.db import Base, check_in, utcnow
from meip.core.ids import uuid7


class PartyRole(StrEnum):
    BORROWER = "borrower"
    GUARANTOR = "guarantor"


class ApplicantStatus(StrEnum):
    ACTIVE = "active"
    REMOVED = "removed"


class RemovalReason(StrEnum):
    ADDED_IN_ERROR = "added_in_error"
    NO_LONGER_APPLYING = "no_longer_applying"
    REPLACED = "replaced"
    OTHER = "other"


REMOVAL_CONSISTENCY = (
    "(status = 'active' AND removed_at IS NULL AND removal_reason IS NULL"
    " AND removal_note IS NULL)"
    " OR (status = 'removed' AND removed_at IS NOT NULL AND removal_reason IS NOT NULL"
    " AND NOT is_primary)"
)
# Only an active borrower can be the primary applicant.
PRIMARY_IS_BORROWER = "NOT is_primary OR party_role = 'borrower'"


class Applicant(Base):
    __tablename__ = "applicants"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "case_id"],
            ["cases.tenant_id", "cases.id"],
            name="fk_applicants_case",
        ),
        # Targets for composite foreign keys: same tenant, and same tenant *and case*.
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "case_id", "id"),
        # Uniqueness applies to active applicants only; removed ones keep their old values.
        Index(
            "uq_applicants_active_display_order",
            "case_id",
            "display_order",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        Index(
            "uq_applicants_active_primary",
            "case_id",
            unique=True,
            postgresql_where=text("is_primary AND status = 'active'"),
        ),
        CheckConstraint("display_order BETWEEN 1 AND 8", name="display_order"),
        CheckConstraint(check_in("party_role", PartyRole), name="party_role"),
        CheckConstraint(check_in("status", ApplicantStatus), name="status"),
        CheckConstraint(
            f"removal_reason IS NULL OR {check_in('removal_reason', RemovalReason)}",
            name="removal_reason",
        ),
        CheckConstraint(REMOVAL_CONSISTENCY, name="removal_consistency"),
        CheckConstraint(PRIMARY_IS_BORROWER, name="primary_is_borrower"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    case_id: Mapped[uuid.UUID] = mapped_column(index=True)
    display_order: Mapped[int] = mapped_column(SmallInteger)
    party_role: Mapped[str] = mapped_column(String(16), default=PartyRole.BORROWER.value)
    on_title: Mapped[bool | None] = mapped_column(Boolean)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(16), default=ApplicantStatus.ACTIVE.value)
    removed_at: Mapped[datetime | None] = mapped_column()
    # Convenience only; the audit log is the authoritative record of who removed (D11).
    removed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    removal_reason: Mapped[str | None] = mapped_column(String(32))
    removal_note: Mapped[str | None] = mapped_column(String(500))
    first_name: Mapped[str] = mapped_column(String(100))
    middle_names: Mapped[str | None] = mapped_column(String(200))
    last_name: Mapped[str] = mapped_column(String(100))
    date_of_birth: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
    version: Mapped[int] = mapped_column(Integer, default=1)

    __mapper_args__ = {"version_id_col": version}  # noqa: RUF012


ACTIVE = Applicant.status == ApplicantStatus.ACTIVE.value
