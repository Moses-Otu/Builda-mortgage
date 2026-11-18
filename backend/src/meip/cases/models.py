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
    PURCHASE = "purchase"
    REMORTGAGE = "remortgage"
    FURTHER_ADVANCE = "further_advance"
    PRODUCT_TRANSFER = "product_transfer"


class CaseStatus(StrEnum):
    DRAFT = "draft"
    COLLECTING_EVIDENCE = "collecting_evidence"
    PROCESSING = "processing"
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    CLOSED = "closed"


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
        CheckConstraint(check_in("status", CaseStatus), name="status"),
        CheckConstraint("term_months IS NULL OR term_months BETWEEN 12 AND 480", name="term"),
        CheckConstraint(
            "property_value_minor IS NULL OR property_value_minor >= 0", name="property_value"
        ),
        CheckConstraint("loan_amount_minor IS NULL OR loan_amount_minor >= 0", name="loan_amount"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    purpose: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default=CaseStatus.DRAFT.value)
    external_reference: Mapped[str | None] = mapped_column(String(100))
    adviser_membership_id: Mapped[uuid.UUID | None] = mapped_column()
    property_value_minor: Mapped[int | None] = mapped_column(BigInteger)
    loan_amount_minor: Mapped[int | None] = mapped_column(BigInteger)
    term_months: Mapped[int | None] = mapped_column(Integer)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
    status_changed_at: Mapped[datetime] = mapped_column(default=utcnow)
    version: Mapped[int] = mapped_column(Integer, default=1)

    __mapper_args__ = {"version_id_col": version}  # noqa: RUF012
