"""Phase 2.0 step 2-C: payslip classification and extraction.

* ``extraction_runs``: one per document version (unique). Records the provider (so synthetic
  results are always identifiable), model, schema and classifier versions, the
  classification and the marker groups found, and the provider's raw output (JSONB) with its
  SHA-256. Insert-only for the runtime role.
* ``extracted_fields``: one row per value, with its own label, raw text, normalised value
  and evidence (page, value box, label box, page size and unit, confidence). Composite
  foreign keys tie it to its run, version and document within one tenant and case. CHECKs
  keep every box inside its page and at most one value column set. Insert-only.
* ``documents``: statuses ``extracted`` and ``unrecognised``; failure code
  ``extraction_failed``. ``jobs``: kind ``document.extract``.
* The downgrade refuses while runs exist.

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "meip_app"
_TS = sa.DateTime(timezone=True)

STATUSES = ("received", "ready", "extracted", "unrecognised", "failed")
OLD_STATUSES = ("received", "ready", "failed")
FAILURE_CODES = (
    "hash_mismatch",
    "missing_original",
    "unreadable_pdf",
    "encrypted_pdf",
    "too_many_pages",
    "timeout",
    "retries_exhausted",
    "extraction_failed",
)
OLD_FAILURE_CODES = FAILURE_CODES[:-1]
JOB_KINDS = ("document.ingest", "document.extract")
FIELD_CODES = (
    "pay_date",
    "pay_period_start",
    "pay_period_end",
    "pay_frequency",
    "tax_code",
    "gross_pay",
    "total_deductions",
    "net_pay",
    "ytd_gross_pay",
    "ytd_income_tax",
    "basic_pay",
    "overtime",
    "bonus",
    "commission",
    "allowance",
    "income_tax",
    "national_insurance",
    "pension_contribution",
    "student_loan_deduction",
)


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _box(prefix: str) -> str:
    x0, y0, x1, y1 = (f"{prefix}{c}" for c in ("x0", "y0", "x1", "y1"))
    return (
        f"0 <= {x0} AND {x0} <= {x1} AND {x1} <= page_width "
        f"AND 0 <= {y0} AND {y0} <= {y1} AND {y1} <= page_height"
    )


def _replace_check(table: str, name: str, sql: str) -> None:
    op.drop_constraint(op.f(f"ck_{table}_{name}"), table, type_="check")
    op.create_check_constraint(name, table, sql)


def upgrade() -> None:
    _replace_check("documents", "status", _in("status", STATUSES))
    _replace_check(
        "documents", "failure_code", f"failure_code IS NULL OR {_in('failure_code', FAILURE_CODES)}"
    )
    _replace_check("jobs", "kind", _in("kind", JOB_KINDS))

    op.create_table(
        "extraction_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("document_version_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("model_version", sa.String(64), nullable=False),
        sa.Column("schema_version", sa.String(32), nullable=False),
        sa.Column("classifier_version", sa.String(32), nullable=False),
        sa.Column("classification", sa.String(16), nullable=False),
        sa.Column("markers", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("raw_output", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("raw_output_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", _TS, nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_extraction_runs")),
        sa.UniqueConstraint(
            "document_version_id", name=op.f("uq_extraction_runs_document_version_id")
        ),
        sa.UniqueConstraint(
            "tenant_id", "case_id", "id", name=op.f("uq_extraction_runs_tenant_id_case_id_id")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_extraction_runs_tenant_id_tenants")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "case_id", "document_version_id"],
            [
                "document_versions.tenant_id",
                "document_versions.case_id",
                "document_versions.id",
            ],
            name=op.f("fk_extraction_runs_document_version"),
        ),
        sa.CheckConstraint("classification IN ('payslip', 'unrecognised')", name="classification"),
        sa.CheckConstraint("raw_output_sha256 ~ '^[0-9a-f]{64}$'", name="raw_output_sha256"),
    )
    op.create_index(op.f("ix_extraction_runs_tenant_id"), "extraction_runs", ["tenant_id"])

    op.create_table(
        "extracted_fields",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("section", sa.String(16), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("field_code", sa.String(32), nullable=True),
        sa.Column("label_text", sa.String(200), nullable=False),
        sa.Column("raw_text", sa.String(200), nullable=False),
        sa.Column("value_money_minor", sa.BigInteger(), nullable=True),
        sa.Column("currency", sa.String(3), nullable=True),
        sa.Column("value_date", sa.Date(), nullable=True),
        sa.Column("value_text", sa.String(64), nullable=True),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("page_width", sa.Float(), nullable=False),
        sa.Column("page_height", sa.Float(), nullable=False),
        sa.Column("unit", sa.String(8), nullable=False),
        sa.Column("x0", sa.Float(), nullable=False),
        sa.Column("y0", sa.Float(), nullable=False),
        sa.Column("x1", sa.Float(), nullable=False),
        sa.Column("y1", sa.Float(), nullable=False),
        sa.Column("label_x0", sa.Float(), nullable=False),
        sa.Column("label_y0", sa.Float(), nullable=False),
        sa.Column("label_x1", sa.Float(), nullable=False),
        sa.Column("label_y1", sa.Float(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_extracted_fields")),
        sa.UniqueConstraint("run_id", "ordinal", name=op.f("uq_extracted_fields_run_id_ordinal")),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_extracted_fields_tenant_id_tenants")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "case_id", "run_id"],
            ["extraction_runs.tenant_id", "extraction_runs.case_id", "extraction_runs.id"],
            name=op.f("fk_extracted_fields_run"),
        ),
        sa.CheckConstraint(
            "section IN ('summary', 'payment', 'deduction', 'year_to_date')", name="section"
        ),
        sa.CheckConstraint(
            f"field_code IS NULL OR {_in('field_code', FIELD_CODES)}", name="field_code"
        ),
        sa.CheckConstraint("ordinal > 0", name="ordinal"),
        sa.CheckConstraint(
            "num_nonnulls(value_money_minor, value_date, value_text) <= 1", name="one_value"
        ),
        sa.CheckConstraint(
            "(currency IS NULL) = (value_money_minor IS NULL) "
            "AND (currency IS NULL OR currency = 'GBP')",
            name="currency",
        ),
        sa.CheckConstraint("page_number >= 1 AND page_width > 0 AND page_height > 0", name="page"),
        sa.CheckConstraint("unit IN ('pt', 'inch')", name="unit"),
        sa.CheckConstraint(_box(""), name="value_box"),
        sa.CheckConstraint(_box("label_"), name="label_box"),
        sa.CheckConstraint("confidence IS NULL OR confidence BETWEEN 0 AND 1", name="confidence"),
    )
    op.create_index(op.f("ix_extracted_fields_tenant_id"), "extracted_fields", ["tenant_id"])

    for table in ("extraction_runs", "extracted_fields"):
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
          IF EXISTS (SELECT 1 FROM extraction_runs)
             OR EXISTS (SELECT 1 FROM jobs WHERE kind = 'document.extract')
             OR EXISTS (SELECT 1 FROM documents
                        WHERE status IN ('extracted', 'unrecognised')
                           OR failure_code = 'extraction_failed') THEN
            RAISE EXCEPTION 'Extraction results exist; downgrading 0014 would discard them';
          END IF;
        END $$
        """
    )
    op.drop_table("extracted_fields")
    op.drop_table("extraction_runs")
    _replace_check("jobs", "kind", _in("kind", JOB_KINDS[:1]))
    _replace_check(
        "documents",
        "failure_code",
        f"failure_code IS NULL OR {_in('failure_code', OLD_FAILURE_CODES)}",
    )
    _replace_check("documents", "status", _in("status", OLD_STATUSES))
