"""Persistent assessment state.

* ``assessments``: one row per run, versioned per case. Final once completed or failed: a
  trigger refuses any change after that, so a result is never overwritten; a new run is a new
  version. At most one queued or running assessment per case (partial unique index).
* ``assessment_steps``: every planner decision and tool call, with its validated input, its
  output, a short rationale (never hidden reasoning) and an idempotency key. Insert-only.
* ``evidence_requests``: contextual requests from the agent or the broker, keyed by gap code;
  at most one open request per gap. Closed as fulfilled when the gap no longer exists.
* ``assessment_decisions``: the broker's decision on an assessment (accept, override with a
  reason, request information, escalate). Insert-only.

Every table is tenant-scoped with RLS and bound to its case by composite foreign keys.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from meip.core.db import Base, check_in, utcnow
from meip.core.ids import uuid7


class AssessmentStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class Outcome(StrEnum):
    POTENTIALLY_ELIGIBLE = "potentially_eligible"
    SUBJECT_TO_VERIFICATION = "eligible_subject_to_verification"
    NOT_CURRENTLY_ELIGIBLE = "not_currently_eligible"
    INSUFFICIENT_INFORMATION = "insufficient_information"


class StepKind(StrEnum):
    TOOL = "tool"
    NOTE = "note"
    ERROR = "error"


class StepStatus(StrEnum):
    OK = "ok"
    ERROR = "error"
    CACHED = "cached"


class RequestStatus(StrEnum):
    OPEN = "open"
    FULFILLED = "fulfilled"
    WITHDRAWN = "withdrawn"


class DecisionKind(StrEnum):
    ACCEPT = "accept"
    OVERRIDE = "override"
    REQUEST_INFORMATION = "request_information"
    ESCALATE = "escalate"


def _case_fk(name: str) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["tenant_id", "case_id"], ["cases.tenant_id", "cases.id"], name=name
    )


def _assessment_fk(name: str, column: str = "assessment_id") -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["tenant_id", "case_id", column],
        ["assessments.tenant_id", "assessments.case_id", "assessments.id"],
        name=name,
    )


class Assessment(Base):
    __tablename__ = "assessments"
    __table_args__ = (
        UniqueConstraint("tenant_id", "case_id", "id"),
        UniqueConstraint("tenant_id", "case_id", "version"),
        _case_fk("fk_assessments_case"),
        CheckConstraint(check_in("status", AssessmentStatus), name="status"),
        CheckConstraint(f"outcome IS NULL OR {check_in('outcome', Outcome)}", name="outcome"),
        CheckConstraint("version >= 1", name="version"),
        CheckConstraint("(status = 'completed') = (outcome IS NOT NULL)", name="outcome_when_done"),
        CheckConstraint("(status = 'failed') = (error_code IS NOT NULL)", name="error_when_failed"),
        CheckConstraint(
            "result_sha256 IS NULL OR result_sha256 ~ '^[0-9a-f]{64}$'", name="result_sha256"
        ),
        Index(
            "uq_assessments_one_active",
            "case_id",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    case_id: Mapped[uuid.UUID] = mapped_column(index=True)
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default=AssessmentStatus.QUEUED.value)
    outcome: Mapped[str | None] = mapped_column(String(40))
    policy_code: Mapped[str] = mapped_column(String(48))
    planner: Mapped[str] = mapped_column(String(16))
    model: Mapped[str | None] = mapped_column(String(100))
    trigger: Mapped[str] = mapped_column(String(16))
    profile_fingerprint: Mapped[str | None] = mapped_column(String(64))
    inputs_sha256: Mapped[str | None] = mapped_column(String(64))
    previous_assessment_id: Mapped[uuid.UUID | None] = mapped_column()
    started_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column()
    finished_at: Mapped[datetime | None] = mapped_column()
    error_code: Mapped[str | None] = mapped_column(String(48))
    summary: Mapped[str | None] = mapped_column(Text)
    result: Mapped[str | None] = mapped_column(Text)
    result_sha256: Mapped[str | None] = mapped_column(String(64))
    step_count: Mapped[int] = mapped_column(Integer, default=0)


class AssessmentStep(Base):
    __tablename__ = "assessment_steps"
    __table_args__ = (
        UniqueConstraint("assessment_id", "seq"),
        _assessment_fk("fk_assessment_steps_assessment"),
        CheckConstraint(check_in("kind", StepKind), name="kind"),
        CheckConstraint(check_in("status", StepStatus), name="status"),
        CheckConstraint("seq >= 1", name="seq"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    case_id: Mapped[uuid.UUID] = mapped_column(index=True)
    assessment_id: Mapped[uuid.UUID] = mapped_column(index=True)
    seq: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(8))
    tool: Mapped[str | None] = mapped_column(String(48))
    rationale: Mapped[str | None] = mapped_column(String(1000))
    input: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    output: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(8))
    error_code: Mapped[str | None] = mapped_column(String(48))
    idempotency_key: Mapped[str | None] = mapped_column(String(64))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class EvidenceRequest(Base):
    __tablename__ = "evidence_requests"
    __table_args__ = (
        _case_fk("fk_evidence_requests_case"),
        _assessment_fk("fk_evidence_requests_assessment"),
        _assessment_fk("fk_evidence_requests_closed_by", "closed_by_assessment_id"),
        ForeignKeyConstraint(
            ["tenant_id", "case_id", "applicant_id"],
            ["applicants.tenant_id", "applicants.case_id", "applicants.id"],
            name="fk_evidence_requests_applicant",
        ),
        CheckConstraint(check_in("status", RequestStatus), name="status"),
        CheckConstraint("requested_by IN ('agent', 'broker')", name="requested_by"),
        CheckConstraint("(status = 'open') = (closed_at IS NULL)", name="closed_consistency"),
        Index(
            "uq_evidence_requests_one_open",
            "case_id",
            "gap_code",
            unique=True,
            postgresql_where=text("status = 'open'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    case_id: Mapped[uuid.UUID] = mapped_column(index=True)
    assessment_id: Mapped[uuid.UUID | None] = mapped_column()
    applicant_id: Mapped[uuid.UUID | None] = mapped_column()
    gap_code: Mapped[str] = mapped_column(String(120))
    category: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(String(300))
    reason: Mapped[str] = mapped_column(String(1000))
    document_type: Mapped[str | None] = mapped_column(String(100))
    months: Mapped[list[str] | None] = mapped_column(JSONB)
    supported_on_platform: Mapped[bool] = mapped_column(Boolean, default=True)
    requested_by: Mapped[str] = mapped_column(String(8))
    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(12), default=RequestStatus.OPEN.value)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    closed_at: Mapped[datetime | None] = mapped_column()
    closed_by_assessment_id: Mapped[uuid.UUID | None] = mapped_column()


class AssessmentDecision(Base):
    __tablename__ = "assessment_decisions"
    __table_args__ = (
        _assessment_fk("fk_assessment_decisions_assessment"),
        CheckConstraint(check_in("decision", DecisionKind), name="decision"),
        CheckConstraint(
            f"override_outcome IS NULL OR {check_in('override_outcome', Outcome)}",
            name="override_outcome",
        ),
        CheckConstraint(
            "(decision = 'override') = (override_outcome IS NOT NULL)", name="override_consistency"
        ),
        CheckConstraint(
            "decision = 'accept' OR length(trim(coalesce(reason, ''))) > 0", name="reason_required"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    case_id: Mapped[uuid.UUID] = mapped_column(index=True)
    assessment_id: Mapped[uuid.UUID] = mapped_column(index=True)
    decision: Mapped[str] = mapped_column(String(24))
    override_outcome: Mapped[str | None] = mapped_column(String(40))
    reason: Mapped[str | None] = mapped_column(String(1000))
    decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
