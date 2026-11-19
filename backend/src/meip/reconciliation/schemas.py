"""API shapes for the reconciliation checks."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import Field, StringConstraints

from meip.core.schemas import InputModel, OutputModel

Note = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]


class EvidenceOut(OutputModel):
    """One cited input. Document evidence resolves to the payslip and field (and so to the
    highlighted box on the original); declared evidence is the applicant's declared figure.
    Statement evidence is a confirmed statement value (by field ordinal); transaction evidence
    is one printed row of a statement (by row number), with its description verbatim."""

    source_kind: Literal["document", "declared", "statement", "transaction"]
    observation_id: uuid.UUID | None = None
    field_review_id: uuid.UUID | None = None
    document_id: uuid.UUID | None = None
    document_label: str | None = Field(default=None, examples=["June 2026 payslip"])
    field_ordinal: int | None = None
    row_number: int | None = None
    label: str
    measure: str | None = None
    value_minor: int | None = None
    value_date: date | None = None
    value_text: str | None = None


class AcknowledgementOut(OutputModel):
    note: str
    acknowledged_by_user_id: uuid.UUID | None
    acknowledged_at: datetime


class RelationshipOut(OutputModel):
    """For a salary credit: whose payslip, which account the money arrived in (sole or joint,
    and its holders), who the statement was filed under, and the matched row."""

    payslip_applicant_id: uuid.UUID
    account_id: uuid.UUID
    account_ending: str
    account_label: str | None
    joint: bool
    holder_ids: list[uuid.UUID]
    applicant_holds_account: bool
    filing_applicant_id: uuid.UUID | None
    document_id: uuid.UUID
    row_number: int


class FindingOut(OutputModel):
    key: str = Field(description="Fingerprint of the rule-set version, rule and cited inputs")
    ruleset_version: str = Field(examples=["payslip-checks/1", "bank-checks/1"])
    rule_code: str
    status: Literal["passed", "discrepancy", "not_run"]
    title: str
    message: str
    income_source_id: uuid.UUID | None = Field(
        default=None, description="Set for payslip checks and salary credits"
    )
    applicant_id: uuid.UUID | None = None
    bank_account_id: uuid.UUID | None = Field(
        default=None, description="Set for bank checks about one account"
    )
    document_id: uuid.UUID | None = Field(
        default=None, description="The statement a bank finding is about, when there is one"
    )
    match_outcome: Literal["exact", "multiple", "near", "none"] | None = Field(
        default=None,
        description="SALARY-CREDIT only. A near match is never treated as received.",
    )
    relationship: RelationshipOut | None = None
    amounts: dict[str, int] = Field(
        default_factory=dict,
        description="Pence: expected, actual, difference and allowance, where relevant",
    )
    evidence: list[EvidenceOut]
    acknowledgement: AcknowledgementOut | None = Field(
        default=None,
        description=(
            "A broker has seen this discrepancy and recorded a note. It is still a "
            "discrepancy: acknowledging resolves nothing and approves no figure."
        ),
    )


class ChecksOut(OutputModel):
    ruleset_versions: list[str] = Field(examples=[["payslip-checks/1", "bank-checks/1"]])
    parameters: dict[str, dict[str, Any]] = Field(
        description="Each rule set's thresholds, by version. The 2 % declared-basic threshold, "
        "the salary match window and the near-match band are initial product assumptions, not "
        "regulatory or lender standards."
    )
    findings: list[FindingOut]


class AcknowledgementIn(InputModel):
    note: Note = Field(
        description="What you found or did. Recorded as written; changes nothing else."
    )
