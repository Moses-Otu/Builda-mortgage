from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import Field, field_validator

from meip.applicants.models import EmploymentStatus
from meip.core.money import Money
from meip.core.schemas import InputModel, Name, OutputModel, ShortText, Version

_EARLIEST_DOB = date(1900, 1, 1)


def _check_dob(value: date | None) -> date | None:
    if value is not None and not (_EARLIEST_DOB <= value <= date.today()):  # noqa: DTZ011
        raise ValueError("date_of_birth must be between 1900-01-01 and today")
    return value


class ApplicantOut(OutputModel):
    id: uuid.UUID
    case_id: uuid.UUID
    position: int = Field(description="1 = first applicant, 2 = second applicant, ...")
    first_name: str
    middle_names: str | None
    last_name: str
    date_of_birth: date | None
    employment_status: EmploymentStatus | None
    declared_gross_annual_income: Money | None = Field(
        description="As declared by the borrower. Not verified."
    )
    declared_monthly_commitments: Money | None = Field(
        description="As declared by the borrower. Not verified."
    )
    created_at: datetime
    updated_at: datetime
    version: int


class ApplicantCreate(InputModel):
    first_name: Name
    middle_names: ShortText | None = None
    last_name: Name
    date_of_birth: date | None = None
    employment_status: EmploymentStatus | None = None
    declared_gross_annual_income: Money | None = None
    declared_monthly_commitments: Money | None = None

    _dob = field_validator("date_of_birth")(_check_dob)


class ApplicantUpdate(InputModel):
    """Partial update. Omitted fields are unchanged; explicit ``null`` clears a field."""

    version: Version
    first_name: Name | None = None
    middle_names: ShortText | None = None
    last_name: Name | None = None
    date_of_birth: date | None = None
    employment_status: EmploymentStatus | None = None
    declared_gross_annual_income: Money | None = None
    declared_monthly_commitments: Money | None = None

    _dob = field_validator("date_of_birth")(_check_dob)
