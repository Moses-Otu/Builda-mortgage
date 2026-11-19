"""Phase 2.0 step 2-B: background processing (minimum viable).

* ``jobs``: one row per piece of background work, tenant-scoped with the standard RLS policy.
  ``UNIQUE (kind, document_version_id)`` makes enqueueing idempotent.
* ``app_claim_job(lock_seconds)``: the one SECURITY DEFINER function. A worker has to find
  work across firms while RLS is on; the function returns IDs only. ``FOR UPDATE SKIP
  LOCKED`` means concurrent workers never get the same job. A ``running`` job whose lock has
  expired (a crashed worker) can be claimed again; each claim increments ``attempts``, which
  the worker uses as a fence when it finishes the job.
* ``documents`` gains ``ready`` / ``failed``, a ``failure_code`` and a ``page_count``. The
  runtime role may UPDATE only those columns (plus ``updated_at`` and ``version``).
  Originals in ``document_versions`` are untouched.
* ``document_versions`` gains ``UNIQUE (tenant_id, case_id, id)`` as the target of the job's
  composite foreign key, so a job can only reference a version in its own tenant and case.
* The downgrade refuses while jobs exist or any document has left ``received``.

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "meip_app"
_TS = sa.DateTime(timezone=True)
FAILURE_CODES = (
    "hash_mismatch",
    "missing_original",
    "unreadable_pdf",
    "encrypted_pdf",
    "too_many_pages",
    "timeout",
    "retries_exhausted",
)
CLAIM_FUNCTION = "app_claim_job(integer)"


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    # --- documents: processing outcome ---------------------------------------------------
    op.drop_constraint(op.f("ck_documents_status"), "documents", type_="check")
    op.create_check_constraint(
        "status", "documents", _in("status", ("received", "ready", "failed"))
    )
    op.add_column("documents", sa.Column("failure_code", sa.String(32), nullable=True))
    op.add_column("documents", sa.Column("page_count", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "failure_code",
        "documents",
        f"failure_code IS NULL OR {_in('failure_code', FAILURE_CODES)}",
    )
    op.create_check_constraint(
        "failure_consistency", "documents", "(status = 'failed') = (failure_code IS NOT NULL)"
    )
    op.create_check_constraint("page_count", "documents", "page_count IS NULL OR page_count > 0")
    op.execute(
        f"GRANT UPDATE (status, failure_code, page_count, updated_at, version) "
        f"ON documents TO {APP_ROLE}"
    )

    op.create_unique_constraint(
        op.f("uq_document_versions_tenant_id_case_id_id"),
        "document_versions",
        ["tenant_id", "case_id", "id"],
    )

    # --- jobs ------------------------------------------------------------------------------
    op.create_table(
        "jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("document_version_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("run_after", _TS, nullable=False),
        sa.Column("locked_until", _TS, nullable=True),
        sa.Column("error_code", sa.String(32), nullable=True),
        sa.Column("created_at", _TS, nullable=False),
        sa.Column("updated_at", _TS, nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_jobs")),
        sa.UniqueConstraint(
            "kind", "document_version_id", name=op.f("uq_jobs_kind_document_version_id")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_jobs_tenant_id_tenants")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "case_id", "document_version_id"],
            [
                "document_versions.tenant_id",
                "document_versions.case_id",
                "document_versions.id",
            ],
            name=op.f("fk_jobs_document_version"),
        ),
        sa.CheckConstraint("kind IN ('document.ingest')", name="kind"),
        sa.CheckConstraint("status IN ('queued', 'running', 'done', 'failed')", name="status"),
        sa.CheckConstraint("attempts >= 0", name="attempts"),
        sa.CheckConstraint(
            "status <> 'running' OR locked_until IS NOT NULL", name="running_is_locked"
        ),
    )
    op.create_index(op.f("ix_jobs_tenant_id"), "jobs", ["tenant_id"])
    op.create_index(
        "ix_jobs_claimable",
        "jobs",
        ["run_after"],
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
    op.execute("ALTER TABLE jobs ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON jobs "
        "USING (tenant_id = app_current_tenant()) "
        "WITH CHECK (tenant_id = app_current_tenant())"
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON jobs TO {APP_ROLE}")

    op.execute(
        """
        CREATE FUNCTION app_claim_job(p_lock_seconds integer)
        RETURNS TABLE (
            job_id uuid, tenant_id uuid, kind text, document_version_id uuid, attempts integer
        )
        LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path = public, pg_temp AS
        $$
          -- The oldest due job, or a running one whose lock expired (its worker died).
          -- SKIP LOCKED: concurrent workers never wait on, or receive, the same row.
          UPDATE jobs j
             SET status = 'running',
                 attempts = j.attempts + 1,
                 locked_until = now() + make_interval(secs => greatest(p_lock_seconds, 1)),
                 updated_at = now()
           WHERE j.id = (
                   SELECT c.id FROM jobs c
                    WHERE (c.status = 'queued' AND c.run_after <= now())
                       OR (c.status = 'running' AND c.locked_until < now())
                    ORDER BY c.run_after, c.created_at, c.id
                    LIMIT 1
                    FOR UPDATE SKIP LOCKED)
          RETURNING j.id, j.tenant_id, j.kind::text, j.document_version_id, j.attempts
        $$
        """
    )
    op.execute(f"REVOKE ALL ON FUNCTION {CLAIM_FUNCTION} FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION {CLAIM_FUNCTION} TO {APP_ROLE}")


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM jobs)
             OR EXISTS (SELECT 1 FROM documents WHERE status <> 'received') THEN
            RAISE EXCEPTION 'Jobs or processed documents exist; downgrading 0013 would '
              'discard their processing state';
          END IF;
        END $$
        """
    )
    op.execute(f"DROP FUNCTION IF EXISTS {CLAIM_FUNCTION}")
    op.drop_table("jobs")
    op.drop_constraint(
        op.f("uq_document_versions_tenant_id_case_id_id"), "document_versions", type_="unique"
    )
    op.execute(
        f"REVOKE UPDATE (status, failure_code, page_count, updated_at, version) "
        f"ON documents FROM {APP_ROLE}"
    )
    for name in ("page_count", "failure_consistency", "failure_code", "status"):
        op.drop_constraint(op.f(f"ck_documents_{name}"), "documents", type_="check")
    op.drop_column("documents", "page_count")
    op.drop_column("documents", "failure_code")
    op.create_check_constraint("status", "documents", "status IN ('received')")
