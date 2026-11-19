"""Document API schemas."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field

from meip.documents.models import DocumentStatus, DocumentType, FailureCode, ScanStatus


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


class ExtractionOut(BaseModel):
    provider: str = Field(description="'synthetic-fixture' marks results from synthetic test data")
    model_version: str
    schema_version: str
    classifier_version: str
    classification: str = Field(description="'payslip' or 'unrecognised'")
    markers: list[str] = Field(description="Classification marker groups found (names only)")
    created_at: datetime
    summary: list[ExtractedFieldOut]
    payments: list[ExtractedFieldOut]
    deductions: list[ExtractedFieldOut]
    year_to_date: list[ExtractedFieldOut]
    missing_required: list[str] = Field(
        description="Required summary fields with no parsed value (pay_date, gross_pay, net_pay)"
    )
