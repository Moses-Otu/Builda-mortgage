"""Document API schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from meip.documents.models import DocumentStatus, DocumentType, ScanStatus


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
    original: DocumentVersionOut
