"""Phase 2.0 step 2-D: review, correct and save payslip figures to the case.

* ``document_reviews``: one per extraction run (unique): the income source the payslip is
  evidence for, the frequency used, the reviewer and time. Insert-only.
* ``field_reviews``: one decision per extracted field per review (unique). A correction holds
  its value beside the original (which stays on the field) and needs a reason; a rejection
  needs a reason. Insert-only.
* ``financial_observations.field_review_id``: every ``document`` observation links to the
  decision it came from, and only ``document`` observations may (CHECK); at most one
  observation per decision (unique). ``source_kind`` gains ``document``; it stays outside the
  current-value index (declared and broker entries only).
* ``documents`` status gains ``reviewed``.
* All links are composite foreign keys within one tenant and case. The downgrade refuses
  while reviews exist.

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "meip_app"
_TS = sa.DateTime(timezone=True)
STATUSES = ("received", "ready", "extracted", "unrecognised", "reviewed", "failed")
OLD_STATUSES = ("received", "ready", "extracted", "unrecognised", "failed")
SOURCE_KINDS = ("declared", "broker_entry", "document")
FREQUENCIES = ("one_off", "weekly", "fortnightly", "four_weekly", "monthly", "quarterly", "annual")


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _replace_check(table: str, name: str, sql: str) -> None:
    op.drop_constraint(op.f(f"ck_{table}_{name}"), table, type_="check")
    op.create_check_constraint(name, table, sql)


def upgrade() -> None:
    _replace_check("documents", "status", _in("status", STATUSES))
    op.create_unique_constraint(
        op.f("uq_extracted_fields_tenant_id_case_id_id"),
        "extracted_fields",
        ["tenant_id", "case_id", "id"],
    )

    op.create_table(
        "document_reviews",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("income_source_id", sa.Uuid(), nullable=False),
        sa.Column("frequency", sa.String(16), nullable=False),
        sa.Column("reviewed_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", _TS, nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_reviews")),
        sa.UniqueConstraint("run_id", name=op.f("uq_document_reviews_run_id")),
        sa.UniqueConstraint(
            "tenant_id", "case_id", "id", name=op.f("uq_document_reviews_tenant_id_case_id_id")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_document_reviews_tenant_id_tenants")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "case_id", "run_id"],
            ["extraction_runs.tenant_id", "extraction_runs.case_id", "extraction_runs.id"],
            name=op.f("fk_document_reviews_run"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "case_id", "income_source_id"],
            ["income_sources.tenant_id", "income_sources.case_id", "income_sources.id"],
            name=op.f("fk_document_reviews_income_source"),
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by_user_id"],
            ["users.id"],
            name=op.f("fk_document_reviews_reviewed_by_user_id_users"),
        ),
        sa.CheckConstraint(_in("frequency", FREQUENCIES), name="frequency"),
    )
    op.create_index(op.f("ix_document_reviews_tenant_id"), "document_reviews", ["tenant_id"])

    op.create_table(
        "field_reviews",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("review_id", sa.Uuid(), nullable=False),
        sa.Column("field_id", sa.Uuid(), nullable=False),
        sa.Column("decision", sa.String(8), nullable=False),
        sa.Column("corrected_money_minor", sa.BigInteger(), nullable=True),
        sa.Column("corrected_date", sa.Date(), nullable=True),
        sa.Column("corrected_text", sa.String(64), nullable=True),
        sa.Column("reason", sa.String(500), nullable=True),
        sa.Column("measure", sa.String(48), nullable=True),
        sa.Column("qualifier", sa.String(32), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_field_reviews")),
        sa.UniqueConstraint(
            "review_id", "field_id", name=op.f("uq_field_reviews_review_id_field_id")
        ),
        sa.UniqueConstraint(
            "tenant_id", "case_id", "id", name=op.f("uq_field_reviews_tenant_id_case_id_id")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_field_reviews_tenant_id_tenants")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "case_id", "review_id"],
            ["document_reviews.tenant_id", "document_reviews.case_id", "document_reviews.id"],
            name=op.f("fk_field_reviews_review"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "case_id", "field_id"],
            ["extracted_fields.tenant_id", "extracted_fields.case_id", "extracted_fields.id"],
            name=op.f("fk_field_reviews_field"),
        ),
        sa.CheckConstraint("decision IN ('accept', 'correct', 'reject')", name="decision"),
        sa.CheckConstraint(
            "num_nonnulls(corrected_money_minor, corrected_date, corrected_text) "
            "= CASE WHEN decision = 'correct' THEN 1 ELSE 0 END",
            name="correction",
        ),
        sa.CheckConstraint(
            # NULL-safe: a CHECK passes on NULL, so test the reason's presence explicitly.
            "decision = 'accept' OR (reason IS NOT NULL AND length(trim(reason)) > 0)",
            name="reason_required",
        ),
        sa.CheckConstraint(
            "decision <> 'reject' OR (measure IS NULL AND qualifier IS NULL)",
            name="rejected_saves_nothing",
        ),
    )
    op.create_index(op.f("ix_field_reviews_tenant_id"), "field_reviews", ["tenant_id"])

    _replace_check("financial_observations", "source_kind", _in("source_kind", SOURCE_KINDS))
    op.add_column("financial_observations", sa.Column("field_review_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        op.f("fk_financial_observations_field_review"),
        "financial_observations",
        "field_reviews",
        ["tenant_id", "case_id", "field_review_id"],
        ["tenant_id", "case_id", "id"],
    )
    op.create_unique_constraint(
        op.f("uq_financial_observations_field_review_id"),
        "financial_observations",
        ["field_review_id"],
    )
    op.create_check_constraint(
        "document_lineage",
        "financial_observations",
        "(source_kind = 'document') = (field_review_id IS NOT NULL)",
    )

    for table in ("document_reviews", "field_reviews"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            "USING (tenant_id = app_current_tenant()) "
            "WITH CHECK (tenant_id = app_current_tenant())"
        )
        op.execute(f"GRANT SELECT, INSERT ON {table} TO {APP_ROLE}")


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM document_reviews)
             OR EXISTS (SELECT 1 FROM financial_observations WHERE source_kind = 'document')
             OR EXISTS (SELECT 1 FROM documents WHERE status = 'reviewed') THEN
            RAISE EXCEPTION 'Document reviews exist; downgrading 0015 would discard them';
          END IF;
        END $$
        """
    )
    op.drop_constraint(
        op.f("ck_financial_observations_document_lineage"), "financial_observations", type_="check"
    )
    op.drop_constraint(
        op.f("uq_financial_observations_field_review_id"), "financial_observations", type_="unique"
    )
    op.drop_constraint(
        op.f("fk_financial_observations_field_review"), "financial_observations", type_="foreignkey"
    )
    op.drop_column("financial_observations", "field_review_id")
    _replace_check("financial_observations", "source_kind", _in("source_kind", SOURCE_KINDS[:2]))
    op.drop_table("field_reviews")
    op.drop_table("document_reviews")
    op.drop_constraint(
        op.f("uq_extracted_fields_tenant_id_case_id_id"), "extracted_fields", type_="unique"
    )
    _replace_check("documents", "status", _in("status", OLD_STATUSES))
