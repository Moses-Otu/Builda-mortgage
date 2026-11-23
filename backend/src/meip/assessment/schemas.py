"""API and result shapes for the agentic assessment. Money is pence (``*_minor``)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import Field, field_validator

from meip.core.schemas import InputModel, OutputModel

INDICATIVE_NOTICE = (
    "Indicative pre-qualification and affordability assessment, computed by versioned rules "
    "from the evidence on file and the stated assumptions. It is not a lending decision, a "
    "mortgage offer or advice. The broker makes every final decision."
)

OutcomeCode = Literal[
    "potentially_eligible",
    "eligible_subject_to_verification",
    "not_currently_eligible",
    "insufficient_information",
]


# --- result building blocks ----------------------------------------------------------------


class PolicyOut(OutputModel):
    code: str
    name: str
    description: str
    parameters: dict[str, Any]


class AssessmentApplicantOut(OutputModel):
    applicant_id: uuid.UUID
    label: str = Field(examples=["Applicant 1"])
    party_role: str
    is_primary: bool
    age_at_term_end: int | None


class IncomeLineOut(OutputModel):
    income_source_id: uuid.UUID
    applicant_label: str
    kind: str
    name: str | None
    basis: str | None
    verification: Literal["verified", "extracted", "declared", "missing"] = Field(
        description="verified: reviewed documents and an exact salary credit; extracted: "
        "reviewed documents only; declared: stated only; missing: no figures"
    )
    recurring: bool
    periods_evidenced: list[str]
    corroborated_periods: list[str]
    gross_monthly_minor: int | None
    net_monthly_minor: int | None
    variable_monthly_minor: int
    qualifying_gross_monthly_minor: int
    qualifying_net_monthly_minor: int | None
    counted: bool
    declared_gross_monthly_minor: int | None
    months_in_employment: int | None
    figure_ids: list[uuid.UUID]
    notes: list[str]


class CommitmentLineOut(OutputModel):
    commitment_id: uuid.UUID
    kind: str
    creditor: str | None
    status: str
    monthly_minor: int | None
    basis: str
    counted: bool
    outstanding_balance_minor: int | None
    notes: list[str]


class LimitOut(OutputModel):
    code: str
    max_loan_minor: int
    explanation: str


class AffordabilityOut(OutputModel):
    policy_code: str
    gross_monthly_income_minor: int
    net_monthly_income_minor: int
    commitments_monthly_minor: int
    living_costs_monthly_minor: int
    living_costs_basis: str
    property_value_minor: int | None
    loan_amount_minor: int | None
    deposit_minor: int | None
    min_deposit_minor: int | None
    rate_percent: Decimal
    stressed_rate_percent: Decimal
    term_months: int
    disposable_before_mortgage_minor: int
    max_affordable_monthly_repayment_minor: int
    limits: list[LimitOut]
    max_mortgage_minor: int | None
    binding_constraint: str | None
    monthly_repayment_minor: int | None
    stressed_monthly_repayment_minor: int | None
    total_repayable_minor: int | None
    disposable_after_mortgage_minor: int | None
    dti_percent: Decimal | None
    ltv_percent: Decimal | None
    lti: Decimal | None
    requested_within_limits: bool | None
    shortfall_minor: int | None
    risks: list[str]
    assumptions: list[str]


class ScenarioOut(OutputModel):
    label: str
    hypothetical: Literal[True] = True
    changed: list[str]
    loan_amount_minor: int | None
    deposit_minor: int | None
    term_months: int
    rate_percent: Decimal
    monthly_repayment_minor: int | None
    stressed_monthly_repayment_minor: int | None
    total_repayable_minor: int | None
    max_mortgage_minor: int | None
    binding_constraint: str | None
    ltv_percent: Decimal | None
    lti: Decimal | None
    dti_percent: Decimal | None
    within_limits: bool | None
    shortfall_minor: int | None
    disposable_after_mortgage_minor: int | None


class CriterionOut(OutputModel):
    code: str
    title: str
    status: Literal["met", "not_met", "unknown", "not_assessed"]
    detail: str
    applicant_label: str | None
    evidence: list[str]


class EligibilityOut(OutputModel):
    policy_code: str
    outcome: OutcomeCode
    outcome_label: str
    criteria: list[CriterionOut]
    reasons: list[str]
    unmet: list[str]
    outstanding: list[str]
    indicative_range_minor: list[int] | None = Field(
        description="[low, high]: verified income only, and all counted income"
    )


class GapOut(OutputModel):
    code: str
    category: Literal["document", "information", "action"]
    title: str
    reason: str
    applicant_id: str | None
    applicant_label: str | None
    document_type: str | None
    months: list[str]
    supported_on_platform: bool
    affects_outcome: bool


class DiscrepancyOut(OutputModel):
    key: str
    rule_code: str
    title: str
    message: str
    applicant_label: str | None
    amounts: dict[str, int]
    acknowledged: bool
    acknowledgement_note: str | None
    analysis: list[str] = Field(description="Deterministic comparison of the evidence")


class AgentFindingOut(OutputModel):
    title: str
    detail: str
    severity: Literal["info", "warning", "critical"]
    refs: list[str]


class EscalationOut(OutputModel):
    reason: str
    refs: list[str]


class ChangeOut(OutputModel):
    kind: str
    detail: str


class ReassessmentOut(OutputModel):
    previous_assessment_id: uuid.UUID
    previous_version: int
    previous_outcome: str | None
    changes: list[ChangeOut]
    affected: list[str] = Field(description="Earlier findings or results these changes affect")


class AssessmentResult(OutputModel):
    """What a completed assessment concluded. Stored as canonical JSON; its SHA-256 is kept."""

    notice: str = INDICATIVE_NOTICE
    policy: PolicyOut
    case: dict[str, Any]
    applicants: list[AssessmentApplicantOut]
    income: list[IncomeLineOut]
    commitments: list[CommitmentLineOut]
    affordability: AffordabilityOut | None
    eligibility: EligibilityOut
    scenarios: list[ScenarioOut]
    evidence_gaps: list[GapOut]
    discrepancies: list[DiscrepancyOut]
    findings: list[AgentFindingOut]
    escalations: list[EscalationOut]
    evidence_request_ids: list[uuid.UUID]
    reassessment: ReassessmentOut | None
    summary: str = Field(description="The agent's explanation (text only; no figures of its own)")
    recommendations: list[str]
    limitations: list[str]
    profile_fingerprint: str
    profile_approved: bool


# --- API ---------------------------------------------------------------------------------


class StartIn(InputModel):
    policy_code: str = Field(default="uk-resi-standard/1", examples=["uk-resi-standard/1"])
    force: bool = Field(
        default=False,
        description="Run again even if nothing changed since the latest completed assessment",
    )


class StepOut(OutputModel):
    seq: int
    kind: str
    tool: str | None
    rationale: str | None
    input: dict[str, Any] | None
    output: dict[str, Any] | None
    status: str
    error_code: str | None
    duration_ms: int | None
    created_at: datetime


class DecisionOut(OutputModel):
    id: uuid.UUID
    decision: str
    override_outcome: str | None
    reason: str | None
    decided_by: str | None
    created_at: datetime


class AssessmentSummaryOut(OutputModel):
    id: uuid.UUID
    case_id: uuid.UUID
    version: int
    status: Literal["queued", "running", "completed", "failed"]
    outcome: OutcomeCode | None
    policy_code: str
    planner: str
    model: str | None
    trigger: str
    profile_fingerprint: str | None
    previous_assessment_id: uuid.UUID | None
    started_by: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    error_code: str | None
    step_count: int
    stale: bool = Field(
        description="The profile or case inputs changed since this assessment ran: re-run it"
    )
    final_outcome: OutcomeCode | None = Field(
        description="The broker's override if any, else the assessed outcome"
    )
    decision: str | None = Field(description="The broker's latest decision, if any")


class AssessmentOut(AssessmentSummaryOut):
    summary: str | None
    result: AssessmentResult | None
    result_sha256: str | None
    steps: list[StepOut]
    decisions: list[DecisionOut]


class StartedOut(OutputModel):
    assessment: AssessmentSummaryOut
    reused: bool = Field(description="Nothing changed: the latest completed assessment is returned")


class DecisionIn(InputModel):
    decision: Literal["accept", "override", "request_information", "escalate"]
    override_outcome: OutcomeCode | None = None
    reason: str | None = Field(default=None, max_length=1000)
    request_title: str | None = Field(default=None, max_length=300)
    request_document_type: str | None = Field(default=None, max_length=100)

    @field_validator("reason", "request_title", "request_document_type")
    @classmethod
    def _strip(cls, v: str | None) -> str | None:
        v = v.strip() if v else v
        return v or None


class EvidenceRequestOut(OutputModel):
    id: uuid.UUID
    assessment_id: uuid.UUID | None
    applicant_id: uuid.UUID | None
    gap_code: str
    category: str
    title: str
    reason: str
    document_type: str | None
    months: list[str] | None
    supported_on_platform: bool
    requested_by: str
    status: str
    created_at: datetime
    closed_at: datetime | None


class ScenarioIn(InputModel):
    label: str = Field(min_length=1, max_length=120)
    loan_amount_minor: int | None = Field(default=None, ge=0, le=10_000_000_000)
    deposit_minor: int | None = Field(default=None, ge=0, le=10_000_000_000)
    term_months: int | None = Field(default=None, ge=12, le=480)
    rate_percent: Decimal | None = Field(default=None, ge=0, le=20)
    include_unverified_income: bool = Field(
        default=False,
        description="Also count income excluded as unverified, as if it were verified",
    )


class ScenariosIn(InputModel):
    policy_code: str = "uk-resi-standard/1"
    scenarios: list[ScenarioIn] = Field(default_factory=list, max_length=8)


class ScenariosOut(OutputModel):
    notice: str = INDICATIVE_NOTICE
    base: AffordabilityOut | None
    scenarios: list[ScenarioOut]


class AsOf(OutputModel):
    today: date
