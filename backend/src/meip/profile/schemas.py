"""API shapes for the financial profile (rule ``profile/1``)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import Field, StringConstraints

from meip.core.schemas import InputModel, OutputModel
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


class ProfileSnapshot(OutputModel):
    """The content a broker attests: the case's financial profile under rule ``profile/1``.
    Every current (not retired) figure, labelled by origin, with its provenance; what supports
    each item; the checks; the documents; and the names of every member referenced. Nothing is
    chosen, averaged, annualised or summed. This is exactly what an approval freezes: its
    canonical JSON is the snapshot, and its SHA-256 the fingerprint."""

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
        description="Per item, what supports it in separate kinds (declared, documentary, "
        "corroborating, findings); approval, the fifth, is profile-level"
    )
    members: list[MemberRef] = Field(
        description="Every firm member the profile references, with their name as it was "
        "when the profile was built, so a stored snapshot stays readable"
    )


# --- readiness and approval (derived; outside the fingerprint and the snapshot) -----------------


class Blocker(OutputModel):
    code: Literal[
        "case_not_open",
        "documents_outstanding",
        "discrepancies_unacknowledged",
        "nothing_recorded",
    ]
    message: str
    ids: list[str] = Field(default_factory=list, description="Documents or finding keys")


class Notice(OutputModel):
    """A fact shown before attesting. Never blocks; never a count or a judgement."""

    message: str


class Attestation(OutputModel):
    version: str = Field(examples=["attestation/1"])
    text: str


class Readiness(OutputModel):
    ready: bool
    blockers: list[Blocker]
    notices: list[Notice]
    attestation: Attestation = Field(description="The wording the broker will attest")


class Superseded(OutputModel):
    version: int
    superseded_at: datetime | None


class ApprovalStatus(OutputModel):
    """The broker's approval of the whole profile. Approval is a broker act, not a property of
    the evidence; its facts live on the approval record, never inside the snapshot."""

    status: Literal["not_approved", "approved"] = "not_approved"
    approval_id: uuid.UUID | None = None
    version: int | None = None
    approved_by: MemberRef | None = None
    approved_at: datetime | None = None
    superseded: list[Superseded] = Field(default_factory=list)


class ProfileOut(ProfileSnapshot):
    """The live profile: the snapshot content plus three derived fields that describe its
    state, not its content (excluded from the fingerprint and from any snapshot)."""

    readiness: Readiness
    approval: ApprovalStatus
    fingerprint: str = Field(
        description="SHA-256 of the profile's canonical JSON, without readiness, approval and "
        "the fingerprint itself. Approving requires the fingerprint the broker saw."
    )


Note = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]


class ApproveIn(InputModel):
    fingerprint: str = Field(
        pattern=r"^[0-9a-f]{64}$", description="The profile's fingerprint as loaded"
    )
    attestation_version: str = Field(examples=["attestation/1"])
    confirmed: bool = Field(description="The broker ticked the attestation")
    note: Note | None = None


class ApprovalOut(OutputModel):
    id: uuid.UUID
    case_id: uuid.UUID
    version: int
    profile_rules: str
    snapshot_sha256: str
    attestation_version: str
    note: str | None
    approved_by: MemberRef | None
    approved_at: datetime
    current: bool = Field(description="The case's current approval")
    superseded_at: datetime | None = Field(
        default=None, description="When reopening the case superseded it"
    )


class ApprovedOut(ApprovalOut):
    case_version: int = Field(description="The case's new version (its ETag)")


class ApprovalDetailOut(ApprovalOut):
    attestation_text: str = Field(description="The exact wording the broker accepted")
    snapshot: ProfileSnapshot = Field(description="The approved profile, its hash checked")
