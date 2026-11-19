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
    Integer,
    String,
    UniqueConstraint,
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
    """Stored and awaiting processing. Later steps add processing states."""


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
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    case_id: Mapped[uuid.UUID] = mapped_column(index=True)
    applicant_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    document_type: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default=DocumentStatus.RECEIVED.value)
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
