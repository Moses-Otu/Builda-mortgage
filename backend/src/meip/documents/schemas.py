"""Document API schemas."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field

from meip.core.schemas import InputModel
from meip.documents.models import (
    Decision,
    DocumentStatus,
    DocumentType,
    FailureCode,
    ScanStatus,
)
from meip.financial.measures import Frequency


class DocumentVersionOut(BaseModel):
    id: uuid.UUID
    version_number: int
    sha256: str = Field(description="SHA-256 of the original, lowercase hex")
    size_bytes: int
    media_type: str
    original_filename: str | None = Field(
        description="As sent in Upload-Filename, sanitised. Personal data: not logged."
    )
    scan_status: ScanStatus
    uploaded_by_user_id: uuid.UUID | None
    uploaded_at: datetime


class DocumentOut(BaseModel):
    id: uuid.UUID
    case_id: uuid.UUID
    applicant_id: uuid.UUID | None
    document_type: DocumentType
    status: DocumentStatus
    created_by_user_id: uuid.UUID | None
    created_at: datetime
    version: int
    failure_code: FailureCode | None = Field(
        description="Why processing stopped, when status is 'failed'"
    )
    page_count: int | None = Field(description="Known once processing has checked the PDF")
    original: DocumentVersionOut


class Evidence(BaseModel):
    """Where a value (or its label) appears: a box on a page, origin top-left."""

    page_number: int
    x0: float
    y0: float
    x1: float
    y1: float


class ExtractedFieldOut(BaseModel):
    ordinal: int
    field_code: str | None = Field(
        description="Canonical code when the label is recognised; otherwise null (line kept)"
    )
    label_text: str = Field(description="The label exactly as printed")
    raw_text: str = Field(description="The value exactly as printed")
    value_money_minor: int | None = Field(
        description="Normalised amount in pence; null if unparsed"
    )
    currency: str | None
    value_date: date | None
    value_text: str | None
    value_evidence: Evidence
    label_evidence: Evidence
    page_width: float
    page_height: float
    unit: str = Field(description="Coordinate unit: 'pt' (PDF points) or 'inch'")
    confidence: float | None = Field(
        description="An indicator from the provider. Never used to approve a value."
    )


class CellOut(BaseModel):
    """One value's provenance: its text exactly as printed, page and box (top-left origin)."""

    text: str
    page: int
    box: list[float] = Field(description="[x0, y0, x1, y1]")


class TransactionCellsOut(BaseModel):
    date: CellOut | None
    date_inherited: bool = Field(description="No date printed on the row: the previous row's")
    description: list[CellOut] = Field(description="Every printed description line")
    paid_out: CellOut | None
    paid_in: CellOut | None
    balance: CellOut | None
    other: list[CellOut] = Field(description="Cells that fit no column (the row is unparsed)")


class TransactionOut(BaseModel):
    """One printed row of a bank statement's table (Stage 2-B)."""

    row_number: int
    kind: str = Field(description="transaction, brought_forward, carried_forward or unparsed")
    posted_on: date | None
    description: str = Field(description="Verbatim; lines joined with line breaks")
    description_truncated: bool
    paid_in_minor: int | None
    paid_out_minor: int | None
    balance_minor: int | None = Field(description="The running balance, when the row prints one")
    cells: TransactionCellsOut
    row_evidence: Evidence
    page_width: float
    page_height: float
    unit: str


class ExtractionOut(BaseModel):
    provider: str = Field(description="'synthetic-fixture' marks results from synthetic test data")
    model_version: str
    schema_version: str
    classifier_version: str
    classification: str = Field(description="'payslip', 'bank_statement' or 'unrecognised'")
    markers: list[str] = Field(description="Classification marker groups found (names only)")
    created_at: datetime
    summary: list[ExtractedFieldOut]
    payments: list[ExtractedFieldOut]
    deductions: list[ExtractedFieldOut]
    year_to_date: list[ExtractedFieldOut]
    missing_required: list[str] = Field(
        description="Required summary fields with no parsed value (payslips: pay_date, "
        "gross_pay, net_pay; statements: period start and end, opening and closing balance)"
    )
    transactions: list[TransactionOut] = Field(
        default_factory=list, description="Bank statements only: every printed table row"
    )


class FieldDecisionIn(InputModel):
    ordinal: int = Field(ge=1, description="The extracted field's ordinal")
    decision: Decision
    corrected_money_minor: int | None = Field(default=None, description="For money fields")
    corrected_date: date | None = Field(default=None, description="For date fields")
    corrected_text: str | None = Field(default=None, max_length=64, description="For text fields")
    reason: str | None = Field(
        default=None, max_length=500, description="Required to correct or reject"
    )
    measure: str | None = Field(
        default=None,
        max_length=48,
        description="Only for an unrecognised payment or deduction line: the measure to save under",
    )
    qualifier: str | None = Field(
        default=None, max_length=32, description="For bonus and allowance; default 'unspecified'"
    )


class ReviewIn(InputModel):
    income_source_id: uuid.UUID = Field(
        description="An existing income source on the case; a payslip never creates one"
    )
    frequency: Frequency | None = Field(
        default=None, description="Needed only when the payslip's frequency can't be read"
    )
    decisions: list[FieldDecisionIn] = Field(
        min_length=1, max_length=500, description="Exactly one decision for every field"
    )


class FieldDecisionOut(BaseModel):
    ordinal: int
    section: str
    field_code: str | None
    label_text: str
    raw_text: str
    extracted_money_minor: int | None
    extracted_date: date | None
    extracted_text: str | None
    decision: Decision
    corrected_money_minor: int | None
    corrected_date: date | None
    corrected_text: str | None
    reason: str | None
    measure: str | None
    qualifier: str | None
    observation_id: uuid.UUID | None = Field(description="The figure saved from this decision")


class ReviewOut(BaseModel):
    id: uuid.UUID
    document_id: uuid.UUID
    income_source_id: uuid.UUID
    frequency: Frequency
    reviewed_by_user_id: uuid.UUID | None
    created_at: datetime
    decisions: list[FieldDecisionOut]
