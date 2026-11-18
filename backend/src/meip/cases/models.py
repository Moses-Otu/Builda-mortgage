"""Mortgage case: the primary business entity."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from meip.core.db import Base, check_in, utcnow
from meip.core.ids import uuid7


class CasePurpose(StrEnum):
    """What the mortgage transaction is. Separate from how the property is used (D2)."""

    PURCHASE = "purchase"
    REMORTGAGE = "remortgage"
    FURTHER_ADVANCE = "further_advance"
    PRODUCT_TRANSFER = "product_transfer"
    PORTING = "porting"


class PropertyUse(StrEnum):
    RESIDENTIAL = "residential"
    BUY_TO_LET = "buy_to_let"
    SECOND_HOME = "second_home"


class CaseStatus(StrEnum):
    DRAFT = "draft"
    COLLECTING_EVIDENCE = "collecting_evidence"
    IN_REVIEW = "in_review"
    VERIFIED = "verified"
    """Requires a durable broker approval record (Phase 9); not yet storable."""
    CLOSED = "closed"


class ClosureReason(StrEnum):
    COMPLETED = "completed"
    NOT_PROCEEDING = "not_proceeding"
    DECLINED_BY_LENDER = "declined_by_lender"
    WITHDRAWN_BY_FIRM = "withdrawn_by_firm"
    DUPLICATE = "duplicate"
    OTHER = "other"


# Statuses the database accepts today. 'verified' is added (with an approval-pointer CHECK)
# only when profile approvals exist, so a case can never be stored as verified without one.
STORABLE_STATUSES = (
    CaseStatus.DRAFT,
    CaseStatus.COLLECTING_EVIDENCE,
    CaseStatus.IN_REVIEW,
    CaseStatus.CLOSED,
)
STATUS_CHECK = "status IN ({})".format(", ".join(f"'{s.value}'" for s in STORABLE_STATUSES))
CLOSURE_CHECK = "(status = 'closed') = (closure_reason IS NOT NULL)"
# Data sanity only. Typical lender ranges are reported as warnings, not enforced (D7).
TERM_CHECK = "term_months IS NULL OR term_months BETWEEN 1 AND 600"


class Case(Base):
    __tablename__ = "cases"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        ForeignKeyConstraint(
            ["tenant_id", "adviser_membership_id"],
            ["memberships.tenant_id", "memberships.id"],
            name="fk_cases_adviser_membership",
        ),
        CheckConstraint(check_in("purpose", CasePurpose), name="purpose"),
        CheckConstraint(STATUS_CHECK, name="status"),
        CheckConstraint(
            f"property_use IS NULL OR {check_in('property_use', PropertyUse)}",
            name="property_use",
        ),
        CheckConstraint(
            f"closure_reason IS NULL OR {check_in('closure_reason', ClosureReason)}",
            name="closure_reason",
        ),
        CheckConstraint(CLOSURE_CHECK, name="closure_consistency"),
        CheckConstraint(TERM_CHECK, name="term"),
        CheckConstraint(
            "property_value_minor IS NULL OR property_value_minor >= 0", name="property_value"
        ),
        CheckConstraint("loan_amount_minor IS NULL OR loan_amount_minor >= 0", name="loan_amount"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    purpose: Mapped[str] = mapped_column(String(32))
    property_use: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default=CaseStatus.DRAFT.value)
    external_reference: Mapped[str | None] = mapped_column(String(100))
    adviser_membership_id: Mapped[uuid.UUID | None] = mapped_column()
    property_value_minor: Mapped[int | None] = mapped_column(BigInteger)
    loan_amount_minor: Mapped[int | None] = mapped_column(BigInteger)
    term_months: Mapped[int | None] = mapped_column(Integer)
    # Nullable: integrations may create cases. The audit log is the authoritative record of
    # who performed an operation (D11).
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
    status_changed_at: Mapped[datetime] = mapped_column(default=utcnow)
    closure_reason: Mapped[str | None] = mapped_column(String(32))
    closure_note: Mapped[str | None] = mapped_column(String(500))
    version: Mapped[int] = mapped_column(Integer, default=1)

    __mapper_args__ = {"version_id_col": version}  # noqa: RUF012
