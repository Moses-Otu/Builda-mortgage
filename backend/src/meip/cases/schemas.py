from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import Field

from meip.cases.models import CasePurpose, CaseStatus
from meip.core.money import Money
from meip.core.schemas import InputModel, OutputModel, Reason, ShortText, Version

TermMonths = Annotated[int, Field(ge=12, le=480, description="Mortgage term in months")]


class CaseOut(OutputModel):
    id: uuid.UUID
    purpose: CasePurpose
    status: CaseStatus
    external_reference: str | None
    adviser_membership_id: uuid.UUID | None
    property_value: Money | None
    loan_amount: Money | None
    term_months: int | None
    applicant_count: int
    created_by_user_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    status_changed_at: datetime
    version: int


class CaseCreate(InputModel):
    purpose: CasePurpose
    external_reference: ShortText | None = Field(
        default=None, max_length=100, description="The firm's own reference, e.g. CRM case ID"
    )
    adviser_membership_id: uuid.UUID | None = None
    property_value: Money | None = None
    loan_amount: Money | None = None
    term_months: TermMonths | None = None


class CaseUpdate(InputModel):
    """Partial update. Omitted fields are unchanged; explicit ``null`` clears a field."""

    version: Version
    purpose: CasePurpose | None = None
    external_reference: ShortText | None = Field(default=None, max_length=100)
    adviser_membership_id: uuid.UUID | None = None
    property_value: Money | None = None
    loan_amount: Money | None = None
    term_months: TermMonths | None = None


class CaseTransition(InputModel):
    to_status: CaseStatus
    version: Version
    reason: Reason | None = Field(default=None, description="Required when closing a case")
