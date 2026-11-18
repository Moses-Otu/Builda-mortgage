"""Applicants on a mortgage case.

Applicants are owned by a single case (no cross-case borrower records in the MVP), which
keeps personal data scoped to the case it was collected for.

``declared_*`` figures are what the borrower *says*; later phases reconcile them against
what documents *evidence*. They are never treated as verified.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    SmallInteger,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from meip.core.db import Base, check_in, utcnow
from meip.core.ids import uuid7


class EmploymentStatus(StrEnum):
    EMPLOYED = "employed"
    SELF_EMPLOYED = "self_employed"
    COMPANY_DIRECTOR = "company_director"
    CONTRACTOR = "contractor"
    RETIRED = "retired"
    NOT_EMPLOYED = "not_employed"
    OTHER = "other"


class Applicant(Base):
    __tablename__ = "applicants"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "case_id"],
            ["cases.tenant_id", "cases.id"],
            name="fk_applicants_case",
        ),
        UniqueConstraint("case_id", "position"),
        # Target for composite (tenant_id, applicant_id) foreign keys from later phases.
        UniqueConstraint("tenant_id", "id"),
        CheckConstraint("position BETWEEN 1 AND 8", name="position"),
        CheckConstraint(
            f"employment_status IS NULL OR {check_in('employment_status', EmploymentStatus)}",
            name="employment_status",
        ),
        CheckConstraint(
            "declared_gross_annual_income_minor IS NULL OR declared_gross_annual_income_minor >= 0",
            name="declared_income",
        ),
        CheckConstraint(
            "declared_monthly_commitments_minor IS NULL OR declared_monthly_commitments_minor >= 0",
            name="declared_commitments",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    case_id: Mapped[uuid.UUID] = mapped_column(index=True)
    position: Mapped[int] = mapped_column(SmallInteger)
    first_name: Mapped[str] = mapped_column(String(100))
    middle_names: Mapped[str | None] = mapped_column(String(200))
    last_name: Mapped[str] = mapped_column(String(100))
    date_of_birth: Mapped[date | None] = mapped_column(Date)
    employment_status: Mapped[str | None] = mapped_column(String(32))
    declared_gross_annual_income_minor: Mapped[int | None] = mapped_column(BigInteger)
    declared_monthly_commitments_minor: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
    version: Mapped[int] = mapped_column(Integer, default=1)

    __mapper_args__ = {"version_id_col": version}  # noqa: RUF012
