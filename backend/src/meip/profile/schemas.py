"""API shapes for the financial profile (rule ``profile/1``)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import Field

from meip.core.schemas import OutputModel
from meip.documents.schemas import BankAccountOut, DocumentOut, ExtractedFieldOut
from meip.financial.schemas import (
    CommitmentOut,
    ExpenditureOut,
    IncomeSourceOut,
    ObservationValue,
)
from meip.reconciliation.schemas import AcknowledgementOut, ChecksOut

Origin = Literal["extracted", "corrected", "declared", "broker_entry"]


class MemberRef(OutputModel):
    """Who did something: the firm member's display name, else their email."""

    user_id: uuid.UUID | None
    name: str = Field(examples=["Alex Broker"])


class ProfileCase(OutputModel):
    id: uuid.UUID
    external_reference: str | None
    purpose: str
    status: str


class ProfileApplicant(OutputModel):
    """Only what the profile needs: no date of birth or contact details."""

    id: uuid.UUID
    display_order: int
    party_role: str
    is_primary: bool
    status: str
    first_name: str
    middle_names: str | None
    last_name: str


class FigureEvidence(OutputModel):
    """Where a document figure came from: the document, the exact field (page and boxes),
    the extraction, and the broker's decision."""

    document_id: uuid.UUID
    document_version_id: uuid.UUID
    document_type: str
    file_name: str | None
    sha256: str = Field(description="SHA-256 of the original document")
    field: ExtractedFieldOut = Field(description="The printed label and value, page and boxes")
    decision: Literal["accept", "correct"]
    correction_reason: str | None = Field(
        description="Why the broker corrected the extracted value (corrections only)"
    )
    run_id: uuid.UUID
    provider: str
    schema_version: str
    reviewed_by: MemberRef | None
    reviewed_at: datetime


class FigureHistory(OutputModel):
    """A value this figure replaced (newest first), kept for provenance; never in the profile."""

    observation_id: uuid.UUID
    value: ObservationValue
    source_kind: str
    recorded_by: MemberRef | None
    recorded_at: datetime
    retired_reason: str | None


class FigureOut(OutputModel):
    id: uuid.UUID = Field(description="The financial observation")
    item_kind: Literal["income_source", "commitment", "expenditure"]
    item_id: uuid.UUID
    measure: str
    qualifier: str | None
    value: ObservationValue
    frequency: str | None
    period_start: date | None
    period_end: date | None
    tax_year: str | None
    as_of_date: date | None
    source_kind: str
    origin: Origin = Field(
        description="extracted (accepted as read), corrected (by the broker, with a reason), "
        "declared (what the applicant says) or broker_entry"
    )
    recorded_by: MemberRef | None
    recorded_at: datetime
    evidence: FigureEvidence | None = Field(description="Document figures only")
    history: list[FigureHistory] = Field(description="Values it replaced, newest first")
    cited_by: list[str] = Field(description="Keys of the check findings that cite this figure")


# --- "Supported by" (step 3-A2): five kinds of support per item, never merged or counted ---


class DocumentarySupport(OutputModel):
    """A document the broker reviewed against this item. It supports only the measures it
    carries, for the period they cover."""

    document_id: uuid.UUID
    document_type: str
    file_name: str | None
    period_start: date | None = Field(description="Earliest period start of its figures")
    period_end: date | None = Field(description="Latest period end of its figures")
    measures: list[str] = Field(description="The measures saved from it as current figures")
    corrected_measures: list[str] = Field(description="Those the broker corrected")
    figures_saved: bool = Field(
        description="False when the document was reviewed against the item but every "
        "figure was rejected"
    )
    reviewed_by: MemberRef | None
    reviewed_at: datetime


class Corroboration(OutputModel):
    """Evidence that confirms one fact about the item without supplying its figures. Today
    only an exact salary credit (SALARY-CREDIT passed): it confirms receipt of the net
    amount, nothing more."""

    kind: Literal["salary_credit"] = "salary_credit"
    finding_key: str
    payslip_document_id: uuid.UUID | None
    credit_date: date | None
    amount_minor: int | None
    account_id: uuid.UUID
    account_ending: str
    account_label: str | None
    joint: bool
    holder_ids: list[uuid.UUID]
    statement_document_id: uuid.UUID
    row_number: int
    scope: str = Field(description="What this corroboration does and doesn't confirm")


class FindingRef(OutputModel):
    key: str
    ruleset_version: str
    rule_code: str
    title: str


class DiscrepancyRef(FindingRef):
    acknowledgement: AcknowledgementOut | None = Field(
        description="Acknowledged (still a discrepancy) or not"
    )


class NotRunRef(FindingRef):
    reason: str


class ItemFindings(OutputModel):
    discrepancies: list[DiscrepancyRef]
    not_run: list[NotRunRef]
    passed: list[FindingRef]


class ItemSupport(OutputModel):
    """What supports one item, by kind. Lists only, never counts or scores: the quantity of
    evidence is not a measure of reliability."""

    item_kind: Literal["income_source", "commitment", "expenditure"]
    item_id: uuid.UUID
    declared: list[uuid.UUID] = Field(description="Ids of the item's declared figures")
    documentary: list[DocumentarySupport]
    corroborating: list[Corroboration]
    findings: ItemFindings


class ApprovalStatus(OutputModel):
    """The broker's approval of the whole profile (Stage 3-B). Approval is a broker act,
    not a property of the evidence."""

    status: Literal["not_approved", "approved", "superseded"] = "not_approved"


class ProfileOut(OutputModel):
    """The case's financial profile under rule ``profile/1``: every current (not retired)
    figure, labelled by origin, with its provenance. Nothing is chosen, averaged, annualised
    or summed. The fingerprint is the SHA-256 of everything else in this document; approving
    (Stage 3-B) requires the fingerprint the broker saw."""

    rules: Literal["profile/1"] = "profile/1"
    case: ProfileCase
    applicants: list[ProfileApplicant]
    income_sources: list[IncomeSourceOut]
    commitments: list[CommitmentOut]
    expenditure: list[ExpenditureOut]
    figures: list[FigureOut]
    bank_accounts: list[BankAccountOut]
    checks: ChecksOut
    documents: list[DocumentOut] = Field(
        description="Every document on the case, including those not relied on"
    )
    support: list[ItemSupport] = Field(
        description="Per item, what supports it in five separate kinds (declared, "
        "documentary, corroborating, findings; approval is profile-level)"
    )
    approval: ApprovalStatus
    fingerprint: str = Field(description="SHA-256 of this profile's canonical JSON")
