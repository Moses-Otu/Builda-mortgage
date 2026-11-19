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
    highlighted box on the original); declared evidence is the applicant's declared figure."""

    source_kind: Literal["document", "declared"]
    observation_id: uuid.UUID | None = None
    field_review_id: uuid.UUID | None = None
    document_id: uuid.UUID | None = None
    document_label: str | None = Field(default=None, examples=["June 2026 payslip"])
    field_ordinal: int | None = None
    label: str
    measure: str | None = None
    value_minor: int | None = None
    value_date: date | None = None
    value_text: str | None = None


class AcknowledgementOut(OutputModel):
    note: str
    acknowledged_by_user_id: uuid.UUID | None
    acknowledged_at: datetime


class FindingOut(OutputModel):
    key: str = Field(description="Fingerprint of the rule-set version, rule and cited inputs")
    rule_code: str
    status: Literal["passed", "discrepancy", "not_run"]
    title: str
    message: str
    income_source_id: uuid.UUID
    applicant_id: uuid.UUID
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
    ruleset_version: str
    parameters: dict[str, Any] = Field(
        description="The rule set's thresholds. The 2 % declared-basic threshold is an initial "
        "product assumption, not a regulatory or lender standard."
    )
    findings: list[FindingOut]


class AcknowledgementIn(InputModel):
    note: Note = Field(
        description="What you found or did. Recorded as written; changes nothing else."
    )
