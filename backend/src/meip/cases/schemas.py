from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field

from meip.cases.models import CasePurpose, CaseStatus, ClosureReason, PropertyUse
from meip.core.money import Money
from meip.core.schemas import InputModel, OutputModel, Reason, ShortText

TermMonths = Annotated[
    int, Field(ge=1, le=600, description="Mortgage term in months (sanity range only)")
]

WarningCode = Literal["loan_exceeds_property_value", "term_outside_typical_range"]


class CaseWarning(OutputModel):
    """A data check that is advisory, not enforced (D7). Lender policy varies."""

    code: WarningCode
    message: str


class CaseOut(OutputModel):
    id: uuid.UUID
    purpose: CasePurpose
    property_use: PropertyUse | None
    status: CaseStatus
    closure_reason: ClosureReason | None
    closure_note: str | None
    external_reference: str | None
    adviser_membership_id: uuid.UUID | None
    property_value: Money | None
    loan_amount: Money | None
    term_months: int | None
    applicant_count: int = Field(description="Active applicants (removed ones excluded)")
    warnings: list[CaseWarning]
    created_by_user_id: uuid.UUID | None = Field(
        description="Null when created by a non-user actor; see the audit history"
    )
    created_at: datetime
    updated_at: datetime
    status_changed_at: datetime
    version: int


class CaseCreate(InputModel):
    purpose: CasePurpose
    property_use: PropertyUse | None = None
    external_reference: ShortText | None = Field(
        default=None, max_length=100, description="The firm's own reference, e.g. CRM case ID"
    )
    adviser_membership_id: uuid.UUID | None = None
    property_value: Money | None = None
    loan_amount: Money | None = Field(
        default=None,
        description="Total borrowing sought. For a further advance: the total, not the increase.",
    )
    term_months: TermMonths | None = None


class CaseUpdate(InputModel):
    """Partial update. Omitted fields are unchanged; explicit ``null`` clears a field.

    Send the case's current ETag in ``If-Match``.
    """

    purpose: CasePurpose | None = None
    property_use: PropertyUse | None = None
    external_reference: ShortText | None = Field(default=None, max_length=100)
    adviser_membership_id: uuid.UUID | None = None
    property_value: Money | None = None
    loan_amount: Money | None = None
    term_months: TermMonths | None = None


class CaseTransition(InputModel):
    """Move a case through its lifecycle. Send the case's current ETag in ``If-Match``.

    * Closing requires ``closure_reason``; ``other`` also requires ``note``.
    * Reopening (from ``closed`` or ``verified``) requires ``note``.
    """

    to_status: CaseStatus
    closure_reason: ClosureReason | None = None
    note: Reason | None = Field(default=None, description="Why; required when reopening")
