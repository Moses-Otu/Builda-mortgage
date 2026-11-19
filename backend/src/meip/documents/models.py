"""Uploaded documents and their immutable original versions (Phase 2.0, step 2-A).

A ``Document`` is the broker's upload within a case; a ``DocumentVersion`` is the stored
original: its SHA-256, size, media type and storage key. Versions are immutable for every
role (database trigger), and identical content cannot be stored twice in one case (unique
index, which also holds under concurrent uploads).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from meip.core.db import Base, check_in, utcnow
from meip.core.ids import uuid7
from meip.documents.extraction import FIELD_CODES
from meip.documents.statements import FIELD_CODES as STATEMENT_FIELD_CODES
from meip.documents.storage import BLOB_KEY_PATTERN

PDF_MEDIA_TYPE = "application/pdf"
# Payslip and bank statement field codes, in a fixed order (the CHECK must match migration 0017).
ALL_FIELD_CODES = sorted(FIELD_CODES | STATEMENT_FIELD_CODES)


class DocumentType(StrEnum):
    PAYSLIP = "payslip"
    BANK_STATEMENT = "bank_statement"
    """Stage 2-B. Local and test only until the privacy gate is passed (plan, section 2.3)."""


class DocumentStatus(StrEnum):
    RECEIVED = "received"
    """Stored and awaiting processing."""
    READY = "ready"
    """The original passed its hash and PDF checks; page_count is known."""
    EXTRACTED = "extracted"
    """Identified as a payslip; extracted fields are available for review."""
    UNRECOGNISED = "unrecognised"
    """Not identified as a payslip; a person needs to look at it."""
    REVIEWED = "reviewed"
    """A broker reviewed every field and saved the approved figures to the case."""
    FAILED = "failed"
    """Processing stopped; failure_code says why."""


class FailureCode(StrEnum):
    HASH_MISMATCH = "hash_mismatch"
    MISSING_ORIGINAL = "missing_original"
    UNREADABLE_PDF = "unreadable_pdf"
    ENCRYPTED_PDF = "encrypted_pdf"
    TOO_MANY_PAGES = "too_many_pages"
    TIMEOUT = "timeout"
    RETRIES_EXHAUSTED = "retries_exhausted"
    EXTRACTION_FAILED = "extraction_failed"
    # Bank statements the extractor refuses rather than guesses (Stage 2-B).
    STATEMENT_MULTIPLE_ACCOUNTS = "statement_multiple_accounts"
    STATEMENT_FOREIGN_CURRENCY = "statement_foreign_currency"
    STATEMENT_NO_TABLE = "statement_no_table"


class JobKind(StrEnum):
    DOCUMENT_INGEST = "document.ingest"
    DOCUMENT_EXTRACT = "document.extract"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class ScanStatus(StrEnum):
    NOT_SCANNED = "not_scanned"
    """No malware scanner configured (local and test). Scanning arrives with Azure (Phase 12)."""


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("tenant_id", "case_id", "id"),
        ForeignKeyConstraint(
            ["tenant_id", "case_id"], ["cases.tenant_id", "cases.id"], name="fk_documents_case"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "case_id", "applicant_id"],
            ["applicants.tenant_id", "applicants.case_id", "applicants.id"],
            name="fk_documents_applicant",
        ),
        CheckConstraint(check_in("document_type", DocumentType), name="document_type"),
        CheckConstraint(check_in("status", DocumentStatus), name="status"),
        CheckConstraint(
            f"failure_code IS NULL OR {check_in('failure_code', FailureCode)}",
            name="failure_code",
        ),
        CheckConstraint(
            "(status = 'failed') = (failure_code IS NOT NULL)", name="failure_consistency"
        ),
        CheckConstraint("page_count IS NULL OR page_count > 0", name="page_count"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    case_id: Mapped[uuid.UUID] = mapped_column(index=True)
    applicant_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    document_type: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default=DocumentStatus.RECEIVED.value)
    failure_code: Mapped[str | None] = mapped_column(String(32))
    page_count: Mapped[int | None] = mapped_column(Integer)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
    version: Mapped[int] = mapped_column(Integer, default=1)

    __mapper_args__ = {"version_id_col": version}  # noqa: RUF012


class DocumentVersion(Base):
    """An immutable original. ``original_filename`` is personal data: erasable, never logged
    and never used in a storage key or a response header."""

    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "case_id", "sha256"),
        UniqueConstraint("tenant_id", "case_id", "id"),
        UniqueConstraint("document_id", "version_number"),
        UniqueConstraint("blob_key"),
        ForeignKeyConstraint(
            ["tenant_id", "case_id", "document_id"],
            ["documents.tenant_id", "documents.case_id", "documents.id"],
            name="fk_document_versions_document",
        ),
        CheckConstraint("version_number = 1", name="version_number"),
        CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="sha256"),
        CheckConstraint("size_bytes > 0", name="size_bytes"),
        CheckConstraint(f"media_type = '{PDF_MEDIA_TYPE}'", name="media_type"),
        CheckConstraint(f"blob_key ~ '{BLOB_KEY_PATTERN}'", name="blob_key"),
        CheckConstraint(check_in("scan_status", ScanStatus), name="scan_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    case_id: Mapped[uuid.UUID] = mapped_column()
    document_id: Mapped[uuid.UUID] = mapped_column(index=True)
    version_number: Mapped[int] = mapped_column(Integer, default=1)
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    media_type: Mapped[str] = mapped_column(String(64))
    blob_key: Mapped[str] = mapped_column(String(200))
    original_filename: Mapped[str | None] = mapped_column(String(255))
    scan_status: Mapped[str] = mapped_column(String(16), default=ScanStatus.NOT_SCANNED.value)
    uploaded_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    uploaded_at: Mapped[datetime] = mapped_column(default=utcnow)


class Job(Base):
    """Background work. Claimed only through ``app_claim_job()`` (SKIP LOCKED); finished by
    the worker in the same transaction as its writes, fenced on ``attempts``."""

    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("kind", "document_version_id"),
        ForeignKeyConstraint(
            ["tenant_id", "case_id", "document_version_id"],
            [
                "document_versions.tenant_id",
                "document_versions.case_id",
                "document_versions.id",
            ],
            name="fk_jobs_document_version",
        ),
        CheckConstraint(check_in("kind", JobKind), name="kind"),
        CheckConstraint(check_in("status", JobStatus), name="status"),
        CheckConstraint("attempts >= 0", name="attempts"),
        CheckConstraint(
            "status <> 'running' OR locked_until IS NOT NULL", name="running_is_locked"
        ),
        Index(
            "ix_jobs_claimable",
            "run_after",
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    case_id: Mapped[uuid.UUID] = mapped_column()
    kind: Mapped[str] = mapped_column(String(32))
    document_version_id: Mapped[uuid.UUID] = mapped_column()
    status: Mapped[str] = mapped_column(String(16), default=JobStatus.QUEUED.value)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    run_after: Mapped[datetime] = mapped_column(default=utcnow)
    locked_until: Mapped[datetime | None] = mapped_column()
    error_code: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class Classification(StrEnum):
    PAYSLIP = "payslip"
    BANK_STATEMENT = "bank_statement"
    UNRECOGNISED = "unrecognised"


class ExtractionRun(Base):
    """One extraction of a document version. Immutable for the runtime role. ``provider``
    names where the layout came from, so synthetic results are always identifiable."""

    __tablename__ = "extraction_runs"
    __table_args__ = (
        UniqueConstraint("document_version_id"),
        UniqueConstraint("tenant_id", "case_id", "id"),
        ForeignKeyConstraint(
            ["tenant_id", "case_id", "document_version_id"],
            [
                "document_versions.tenant_id",
                "document_versions.case_id",
                "document_versions.id",
            ],
            name="fk_extraction_runs_document_version",
        ),
        CheckConstraint(check_in("classification", Classification), name="classification"),
        CheckConstraint("raw_output_sha256 ~ '^[0-9a-f]{64}$'", name="raw_output_sha256"),
        CheckConstraint(
            "account_fingerprint IS NULL OR account_fingerprint ~ '^[0-9a-f]{64}$'",
            name="account_fingerprint",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    case_id: Mapped[uuid.UUID] = mapped_column()
    document_version_id: Mapped[uuid.UUID] = mapped_column()
    provider: Mapped[str] = mapped_column(String(64))
    model_version: Mapped[str] = mapped_column(String(64))
    schema_version: Mapped[str] = mapped_column(String(32))
    classifier_version: Mapped[str] = mapped_column(String(32))
    classification: Mapped[str] = mapped_column(String(16))
    markers: Mapped[list[str]] = mapped_column(JSONB)
    raw_output: Mapped[dict[str, Any]] = mapped_column(JSONB, deferred=True)
    raw_output_sha256: Mapped[str] = mapped_column(String(64))
    account_fingerprint: Mapped[str | None] = mapped_column(String(64))
    """Bank statements: keyed HMAC of the sort code and account number (never the numbers)."""
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


def _box_check(prefix: str) -> str:
    x0, y0, x1, y1 = (f"{prefix}{c}" for c in ("x0", "y0", "x1", "y1"))
    return (
        f"0 <= {x0} AND {x0} <= {x1} AND {x1} <= page_width "
        f"AND 0 <= {y0} AND {y0} <= {y1} AND {y1} <= page_height"
    )


class ExtractedField(Base):
    """One extracted value with its own label and evidence. Immutable for the runtime role.

    The database guarantees the links (field -> run -> version -> document, one tenant and
    case) and that both boxes lie inside the page. It cannot verify that a box really shows
    the value: the broker checks that in review."""

    __tablename__ = "extracted_fields"
    __table_args__ = (
        UniqueConstraint("run_id", "ordinal"),
        UniqueConstraint("tenant_id", "case_id", "id"),
        ForeignKeyConstraint(
            ["tenant_id", "case_id", "run_id"],
            ["extraction_runs.tenant_id", "extraction_runs.case_id", "extraction_runs.id"],
            name="fk_extracted_fields_run",
        ),
        CheckConstraint(
            "section IN ('summary', 'payment', 'deduction', 'year_to_date')", name="section"
        ),
        CheckConstraint(
            f"field_code IS NULL OR field_code IN ({', '.join(repr(c) for c in ALL_FIELD_CODES)})",
            name="field_code",
        ),
        CheckConstraint("ordinal > 0", name="ordinal"),
        CheckConstraint(
            "num_nonnulls(value_money_minor, value_date, value_text) <= 1", name="one_value"
        ),
        CheckConstraint(
            "(currency IS NULL) = (value_money_minor IS NULL) "
            "AND (currency IS NULL OR currency = 'GBP')",
            name="currency",
        ),
        CheckConstraint("page_number >= 1 AND page_width > 0 AND page_height > 0", name="page"),
        CheckConstraint("unit IN ('pt', 'inch')", name="unit"),
        CheckConstraint(_box_check(""), name="value_box"),
        CheckConstraint(_box_check("label_"), name="label_box"),
        CheckConstraint("confidence IS NULL OR confidence BETWEEN 0 AND 1", name="confidence"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    case_id: Mapped[uuid.UUID] = mapped_column()
    run_id: Mapped[uuid.UUID] = mapped_column()
    section: Mapped[str] = mapped_column(String(16))
    ordinal: Mapped[int] = mapped_column(Integer)
    field_code: Mapped[str | None] = mapped_column(String(32))
    label_text: Mapped[str] = mapped_column(String(200))
    raw_text: Mapped[str] = mapped_column(String(200))
    value_money_minor: Mapped[int | None] = mapped_column(BigInteger)
    currency: Mapped[str | None] = mapped_column(String(3))
    value_date: Mapped[date | None] = mapped_column(Date)
    value_text: Mapped[str | None] = mapped_column(String(64))
    page_number: Mapped[int] = mapped_column(Integer)
    page_width: Mapped[float] = mapped_column(Float)
    page_height: Mapped[float] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(8))
    x0: Mapped[float] = mapped_column(Float)
    y0: Mapped[float] = mapped_column(Float)
    x1: Mapped[float] = mapped_column(Float)
    y1: Mapped[float] = mapped_column(Float)
    label_x0: Mapped[float] = mapped_column(Float)
    label_y0: Mapped[float] = mapped_column(Float)
    label_x1: Mapped[float] = mapped_column(Float)
    label_y1: Mapped[float] = mapped_column(Float)
    confidence: Mapped[float | None] = mapped_column(Float)


TRANSACTION_KINDS = ("transaction", "brought_forward", "carried_forward", "unparsed")


class ExtractedTransaction(Base):
    """One printed row of a bank statement's transactions table (Stage 2-B). Immutable for
    the runtime role.

    The description is kept **verbatim** (multi-line descriptions joined with line breaks);
    money in and money out stay in separate columns, as printed. ``cells`` holds each value's
    own raw text, page and box (date, every description line, paid in, paid out, balance), so
    every figure traces to its place on the original. A row that couldn't be read is kept as
    ``unparsed`` with its raw text and box, never dropped."""

    __tablename__ = "extracted_transactions"
    __table_args__ = (
        UniqueConstraint("run_id", "row_number"),
        ForeignKeyConstraint(
            ["tenant_id", "case_id", "run_id"],
            ["extraction_runs.tenant_id", "extraction_runs.case_id", "extraction_runs.id"],
            name="fk_extracted_transactions_run",
        ),
        CheckConstraint(
            "kind IN (" + ", ".join(repr(k) for k in TRANSACTION_KINDS) + ")", name="kind"
        ),
        CheckConstraint("row_number > 0", name="row_number"),
        CheckConstraint(
            "(paid_in_minor IS NULL OR paid_in_minor >= 0) "
            "AND (paid_out_minor IS NULL OR paid_out_minor >= 0)",
            name="amounts_not_negative",
        ),
        CheckConstraint(
            "kind <> 'transaction' OR num_nonnulls(paid_in_minor, paid_out_minor) >= 1",
            name="transaction_has_amount",
        ),
        CheckConstraint(
            "kind = 'transaction' OR (paid_in_minor IS NULL AND paid_out_minor IS NULL)",
            name="only_transactions_have_amounts",
        ),
        CheckConstraint(
            "kind <> 'unparsed' OR balance_minor IS NULL", name="unparsed_has_no_balance"
        ),
        CheckConstraint("jsonb_typeof(cells) = 'object'", name="cells"),
        CheckConstraint("page_number >= 1 AND page_width > 0 AND page_height > 0", name="page"),
        CheckConstraint("unit IN ('pt', 'inch')", name="unit"),
        CheckConstraint(_box_check(""), name="row_box"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    case_id: Mapped[uuid.UUID] = mapped_column()
    run_id: Mapped[uuid.UUID] = mapped_column(index=True)
    row_number: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(16))
    posted_on: Mapped[date | None] = mapped_column(Date)
    date_inherited: Mapped[bool] = mapped_column(Boolean, default=False)
    description: Mapped[str] = mapped_column(String(500))
    description_truncated: Mapped[bool] = mapped_column(Boolean, default=False)
    paid_in_minor: Mapped[int | None] = mapped_column(BigInteger)
    paid_out_minor: Mapped[int | None] = mapped_column(BigInteger)
    balance_minor: Mapped[int | None] = mapped_column(BigInteger)
    cells: Mapped[dict[str, Any]] = mapped_column(JSONB)
    page_number: Mapped[int] = mapped_column(Integer)
    page_width: Mapped[float] = mapped_column(Float)
    page_height: Mapped[float] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(8))
    x0: Mapped[float] = mapped_column(Float)
    y0: Mapped[float] = mapped_column(Float)
    x1: Mapped[float] = mapped_column(Float)
    y1: Mapped[float] = mapped_column(Float)


class Decision(StrEnum):
    ACCEPT = "accept"
    CORRECT = "correct"
    REJECT = "reject"


class DocumentReview(Base):
    """A broker's review of one extraction run: the income source the payslip is evidence
    for, and the frequency used. One per run. Insert-only for the runtime role."""

    __tablename__ = "document_reviews"
    __table_args__ = (
        UniqueConstraint("run_id"),
        UniqueConstraint("tenant_id", "case_id", "id"),
        ForeignKeyConstraint(
            ["tenant_id", "case_id", "run_id"],
            ["extraction_runs.tenant_id", "extraction_runs.case_id", "extraction_runs.id"],
            name="fk_document_reviews_run",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "case_id", "income_source_id"],
            ["income_sources.tenant_id", "income_sources.case_id", "income_sources.id"],
            name="fk_document_reviews_income_source",
        ),
        CheckConstraint(
            "frequency IN ('one_off', 'weekly', 'fortnightly', 'four_weekly', 'monthly', "
            "'quarterly', 'annual')",
            name="frequency",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    case_id: Mapped[uuid.UUID] = mapped_column()
    run_id: Mapped[uuid.UUID] = mapped_column()
    income_source_id: Mapped[uuid.UUID] = mapped_column()
    frequency: Mapped[str] = mapped_column(String(16))
    reviewed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class FieldReview(Base):
    """One decision on one extracted field. A correction sits beside the original value,
    which stays untouched on the field. Insert-only for the runtime role."""

    __tablename__ = "field_reviews"
    __table_args__ = (
        UniqueConstraint("review_id", "field_id"),
        UniqueConstraint("tenant_id", "case_id", "id"),
        ForeignKeyConstraint(
            ["tenant_id", "case_id", "review_id"],
            ["document_reviews.tenant_id", "document_reviews.case_id", "document_reviews.id"],
            name="fk_field_reviews_review",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "case_id", "field_id"],
            ["extracted_fields.tenant_id", "extracted_fields.case_id", "extracted_fields.id"],
            name="fk_field_reviews_field",
        ),
        CheckConstraint(check_in("decision", Decision), name="decision"),
        CheckConstraint(
            "num_nonnulls(corrected_money_minor, corrected_date, corrected_text) "
            "= CASE WHEN decision = 'correct' THEN 1 ELSE 0 END",
            name="correction",
        ),
        CheckConstraint(
            "decision = 'accept' OR (reason IS NOT NULL AND length(trim(reason)) > 0)",
            name="reason_required",
        ),
        CheckConstraint(
            "decision <> 'reject' OR (measure IS NULL AND qualifier IS NULL)",
            name="rejected_saves_nothing",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    case_id: Mapped[uuid.UUID] = mapped_column()
    review_id: Mapped[uuid.UUID] = mapped_column()
    field_id: Mapped[uuid.UUID] = mapped_column()
    decision: Mapped[str] = mapped_column(String(8))
    corrected_money_minor: Mapped[int | None] = mapped_column(BigInteger)
    corrected_date: Mapped[date | None] = mapped_column(Date)
    corrected_text: Mapped[str | None] = mapped_column(String(64))
    reason: Mapped[str | None] = mapped_column(String(500))
    measure: Mapped[str | None] = mapped_column(String(48))
    qualifier: Mapped[str | None] = mapped_column(String(32))
