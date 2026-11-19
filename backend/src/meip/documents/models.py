"""Uploaded documents and their immutable original versions (Phase 2.0, step 2-A).

A ``Document`` is the broker's upload within a case; a ``DocumentVersion`` is the stored
original: its SHA-256, size, media type and storage key. Versions are immutable for every
role (database trigger), and identical content cannot be stored twice in one case (unique
index, which also holds under concurrent uploads).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from meip.core.db import Base, check_in, utcnow
from meip.core.ids import uuid7
from meip.documents.storage import BLOB_KEY_PATTERN

PDF_MEDIA_TYPE = "application/pdf"


class DocumentType(StrEnum):
    PAYSLIP = "payslip"
    """Phase 2.0 accepts payslips only."""


class DocumentStatus(StrEnum):
    RECEIVED = "received"
    """Stored and awaiting processing."""
    READY = "ready"
    """The original passed its hash and PDF checks; page_count is known."""
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


class JobKind(StrEnum):
    DOCUMENT_INGEST = "document.ingest"


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
