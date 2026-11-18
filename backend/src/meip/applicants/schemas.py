from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import Field, field_validator

from meip.applicants.models import ApplicantStatus, PartyRole, RemovalReason
from meip.core.schemas import InputModel, Name, OutputModel, Reason, ShortText

_EARLIEST_DOB = date(1900, 1, 1)


def _check_dob(value: date | None) -> date | None:
    if value is not None and not (_EARLIEST_DOB <= value <= date.today()):  # noqa: DTZ011
        raise ValueError("date_of_birth must be between 1900-01-01 and today")
    return value


class ApplicantOut(OutputModel):
    id: uuid.UUID = Field(description="Stable for the life of the case; never reused")
    case_id: uuid.UUID
    display_order: int = Field(description="Presentation order among active applicants")
    party_role: PartyRole
    on_title: bool | None = Field(description="Will own the property. Null: not yet known.")
    is_primary: bool = Field(description="The case's primary (first-named) borrower")
    status: ApplicantStatus
    removed_at: datetime | None
    removal_reason: RemovalReason | None
    removal_note: str | None
    first_name: str
    middle_names: str | None
    last_name: str
    date_of_birth: date | None
    created_at: datetime
    updated_at: datetime
    version: int


class ApplicantCreate(InputModel):
    """The first active borrower on a case becomes its primary applicant automatically."""

    party_role: PartyRole = PartyRole.BORROWER
    on_title: bool | None = None
    first_name: Name
    middle_names: ShortText | None = None
    last_name: Name
    date_of_birth: date | None = None

    _dob = field_validator("date_of_birth")(_check_dob)


class ApplicantUpdate(InputModel):
    """Partial update. Omitted fields are unchanged; explicit ``null`` clears a field.

    Send the applicant's current ETag in ``If-Match``. Setting ``is_primary: true`` moves the
    primary flag to this applicant; it cannot be cleared directly.
    """

    party_role: PartyRole | None = None
    on_title: bool | None = None
    is_primary: Literal[True] | None = None
    first_name: Name | None = None
    middle_names: ShortText | None = None
    last_name: Name | None = None
    date_of_birth: date | None = None

    _dob = field_validator("date_of_birth")(_check_dob)


class ApplicantRemoval(InputModel):
    """Remove an applicant from the case. Nothing is deleted; send its ETag in ``If-Match``."""

    removal_reason: RemovalReason
    note: Reason | None = Field(default=None, description="Required when the reason is 'other'")
    new_primary_applicant_id: uuid.UUID | None = Field(
        default=None,
        description=(
            "Required when removing the primary applicant while other active borrowers remain"
        ),
    )


class ApplicantReinstatement(InputModel):
    """Return a removed applicant to the case. Send its ETag in ``If-Match``."""

    note: Reason = Field(description="Why the applicant is being reinstated")
