"""Agentic assessment state: assessments, their steps, evidence requests, broker decisions.

* ``assessments``: versioned per case; at most one queued or running per case (partial unique
  index). The runtime role may update only the run columns, and a trigger refuses any change
  once an assessment is completed or failed: results are never overwritten.
* ``assessment_steps`` and ``assessment_decisions``: insert-only for the runtime role.
* ``evidence_requests``: at most one open request per case and gap; the runtime role may only
  close one (status, closed_at, closed_by_assessment_id).

All four are tenant-scoped with the standard RLS policy and composite foreign keys to their
case (and assessment, and applicant).

Revision ID: 0021
Revises: 0020
Create Date: 2026-11-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "meip_app"
_TS = sa.DateTime(timezone=True)
TABLES = ("assessments", "assessment_steps", "evidence_requests", "assessment_decisions")
OUTCOMES = (
    "potentially_eligible",
    "eligible_subject_to_verification",
    "not_currently_eligible",
    "insufficient_information",
)


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _case_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id", "case_id"], ["cases.tenant_id", "cases.id"], name=f"fk_{table}_case"
    )


def _assessment_fk(name: str, column: str = "assessment_id") -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id", "case_id", column],
        ["assessments.tenant_id", "assessments.case_id", "assessments.id"],
        name=name,
    )


def _tenant_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id"], ["tenants.id"], name=op.f(f"fk_{table}_tenant_id_tenants")
    )


def upgrade() -> None:
    op.create_table(
        "assessments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("outcome", sa.String(40), nullable=True),
        sa.Column("policy_code", sa.String(48), nullable=False),
        sa.Column("planner", sa.String(16), nullable=False),
        sa.Column("model", sa.String(100), nullable=True),
        sa.Column("trigger", sa.String(16), nullable=False),
        sa.Column("profile_fingerprint", sa.String(64), nullable=True),
        sa.Column("inputs_sha256", sa.String(64), nullable=True),
        sa.Column("previous_assessment_id", sa.Uuid(), nullable=True),
        sa.Column("started_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", _TS, nullable=False),
        sa.Column("started_at", _TS, nullable=True),
        sa.Column("finished_at", _TS, nullable=True),
        sa.Column("error_code", sa.String(48), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("result", sa.Text(), nullable=True),
        sa.Column("result_sha256", sa.String(64), nullable=True),
        sa.Column("step_count", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_assessments")),
        sa.UniqueConstraint(
            "tenant_id", "case_id", "id", name=op.f("uq_assessments_tenant_id_case_id_id")
        ),
        sa.UniqueConstraint(
            "tenant_id", "case_id", "version", name=op.f("uq_assessments_tenant_id_case_id_version")
        ),
        _tenant_fk("assessments"),
        _case_fk("assessments"),
        sa.ForeignKeyConstraint(
            ["started_by_user_id"],
            ["users.id"],
            name=op.f("fk_assessments_started_by_user_id_users"),
        ),
        sa.CheckConstraint(
            _in("status", ("queued", "running", "completed", "failed")), name="status"
        ),
        sa.CheckConstraint(f"outcome IS NULL OR {_in('outcome', OUTCOMES)}", name="outcome"),
        sa.CheckConstraint("version >= 1", name="version"),
        sa.CheckConstraint(
            "(status = 'completed') = (outcome IS NOT NULL)", name="outcome_when_done"
        ),
        sa.CheckConstraint(
            "(status = 'failed') = (error_code IS NOT NULL)", name="error_when_failed"
        ),
        sa.CheckConstraint(
            "result_sha256 IS NULL OR result_sha256 ~ '^[0-9a-f]{64}$'", name="result_sha256"
        ),
    )
    op.create_index(
        "uq_assessments_one_active",
        "assessments",
        ["case_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )

    op.create_table(
        "assessment_steps",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("assessment_id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("tool", sa.String(48), nullable=True),
        sa.Column("rationale", sa.String(1000), nullable=True),
        sa.Column("input", JSONB(), nullable=True),
        sa.Column("output", JSONB(), nullable=True),
        sa.Column("status", sa.String(8), nullable=False),
        sa.Column("error_code", sa.String(48), nullable=True),
        sa.Column("idempotency_key", sa.String(64), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", _TS, nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_assessment_steps")),
        sa.UniqueConstraint(
            "assessment_id", "seq", name=op.f("uq_assessment_steps_assessment_id_seq")
        ),
        _tenant_fk("assessment_steps"),
        _assessment_fk("fk_assessment_steps_assessment"),
        sa.CheckConstraint(_in("kind", ("tool", "note", "error")), name="kind"),
        sa.CheckConstraint(_in("status", ("ok", "error", "cached")), name="status"),
        sa.CheckConstraint("seq >= 1", name="seq"),
    )

    op.create_table(
        "evidence_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("assessment_id", sa.Uuid(), nullable=True),
        sa.Column("applicant_id", sa.Uuid(), nullable=True),
        sa.Column("gap_code", sa.String(120), nullable=False),
        sa.Column("category", sa.String(16), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("reason", sa.String(1000), nullable=False),
        sa.Column("document_type", sa.String(100), nullable=True),
        sa.Column("months", JSONB(), nullable=True),
        sa.Column("supported_on_platform", sa.Boolean(), nullable=False),
        sa.Column("requested_by", sa.String(8), nullable=False),
        sa.Column("requested_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("created_at", _TS, nullable=False),
        sa.Column("closed_at", _TS, nullable=True),
        sa.Column("closed_by_assessment_id", sa.Uuid(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evidence_requests")),
        _tenant_fk("evidence_requests"),
        _case_fk("evidence_requests"),
        _assessment_fk("fk_evidence_requests_assessment"),
        _assessment_fk("fk_evidence_requests_closed_by", "closed_by_assessment_id"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "case_id", "applicant_id"],
            ["applicants.tenant_id", "applicants.case_id", "applicants.id"],
            name="fk_evidence_requests_applicant",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id"],
            ["users.id"],
            name=op.f("fk_evidence_requests_requested_by_user_id_users"),
        ),
        sa.CheckConstraint(_in("status", ("open", "fulfilled", "withdrawn")), name="status"),
        sa.CheckConstraint("requested_by IN ('agent', 'broker')", name="requested_by"),
        sa.CheckConstraint("(status = 'open') = (closed_at IS NULL)", name="closed_consistency"),
    )
    op.create_index(
        "uq_evidence_requests_one_open",
        "evidence_requests",
        ["case_id", "gap_code"],
        unique=True,
        postgresql_where=sa.text("status = 'open'"),
    )

    op.create_table(
        "assessment_decisions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("assessment_id", sa.Uuid(), nullable=False),
        sa.Column("decision", sa.String(24), nullable=False),
        sa.Column("override_outcome", sa.String(40), nullable=True),
        sa.Column("reason", sa.String(1000), nullable=True),
        sa.Column("decided_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", _TS, nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_assessment_decisions")),
        _tenant_fk("assessment_decisions"),
        _assessment_fk("fk_assessment_decisions_assessment"),
        sa.ForeignKeyConstraint(
            ["decided_by_user_id"],
            ["users.id"],
            name=op.f("fk_assessment_decisions_decided_by_user_id_users"),
        ),
        sa.CheckConstraint(
            _in("decision", ("accept", "override", "request_information", "escalate")),
            name="decision",
        ),
        sa.CheckConstraint(
            f"override_outcome IS NULL OR {_in('override_outcome', OUTCOMES)}",
            name="override_outcome",
        ),
        sa.CheckConstraint(
            "(decision = 'override') = (override_outcome IS NOT NULL)", name="override_consistency"
        ),
        sa.CheckConstraint(
            "decision = 'accept' OR length(trim(coalesce(reason, ''))) > 0", name="reason_required"
        ),
    )

    indexed = {
        "assessments": ("tenant_id", "case_id"),
        "assessment_steps": ("tenant_id", "case_id", "assessment_id"),
        "evidence_requests": ("tenant_id", "case_id"),
        "assessment_decisions": ("tenant_id", "case_id", "assessment_id"),
    }
    for table, columns in indexed.items():
        for column in columns:
            op.create_index(op.f(f"ix_{table}_{column}"), table, [column])

    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            "USING (tenant_id = app_current_tenant()) "
            "WITH CHECK (tenant_id = app_current_tenant())"
        )

    op.execute(f"GRANT SELECT, INSERT ON assessments TO {APP_ROLE}")
    op.execute(
        "GRANT UPDATE (status, outcome, planner, model, profile_fingerprint, inputs_sha256, "
        "started_at, finished_at, error_code, summary, result, result_sha256, step_count) "
        f"ON assessments TO {APP_ROLE}"
    )
    op.execute(f"GRANT SELECT, INSERT ON assessment_steps TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT ON assessment_decisions TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT ON evidence_requests TO {APP_ROLE}")
    op.execute(
        f"GRANT UPDATE (status, closed_at, closed_by_assessment_id) ON evidence_requests TO {APP_ROLE}"
    )

    # A finished assessment is final for every role: a new run is a new version.
    op.execute(
        """
        CREATE FUNCTION assessments_final() RETURNS trigger
        LANGUAGE plpgsql AS
        $$ BEGIN
             IF OLD.status IN ('completed', 'failed') THEN
               RAISE EXCEPTION 'a finished assessment is final (% refused)', TG_OP
                 USING ERRCODE = 'integrity_constraint_violation';
             END IF;
             RETURN NEW;
           END $$
        """
    )
    op.execute(
        "CREATE TRIGGER assessments_final BEFORE UPDATE "
        "ON assessments FOR EACH ROW EXECUTE FUNCTION assessments_final()"
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM assessments) THEN
            RAISE EXCEPTION 'Assessments exist; downgrading 0021 would discard them';
          END IF;
        END $$
        """
    )
    op.execute("DROP TRIGGER IF EXISTS assessments_final ON assessments")
    op.execute("DROP FUNCTION IF EXISTS assessments_final()")
    for table in reversed(TABLES):
        op.drop_table(table)
