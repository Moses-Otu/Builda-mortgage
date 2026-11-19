"""Stage 2-B step 2B-1: bank statements are uploaded, classified and extracted.

* ``documents.document_type`` gains ``bank_statement``; ``failure_code`` gains the three
  statement refusals (multiple accounts, foreign currency, no transactions table).
* ``extraction_runs.classification`` gains ``bank_statement``; runs gain
  ``account_fingerprint`` (a keyed HMAC of the sort code and account number, never the
  numbers themselves).
* ``extracted_fields.field_code`` gains the statement summary codes (account ending, period,
  opening and closing balance, totals in and out).
* ``extracted_transactions``: one row per printed table row, verbatim descriptions, money in
  and out kept separate, each value's page and box in ``cells``. Insert-only, RLS.

The account and holder tables arrive with statement confirmation (2B-2), where they are first
used. The downgrade refuses while any statement exists.

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "meip_app"
DOCUMENT_TYPES = ("payslip", "bank_statement")
FAILURE_CODES = (
    "hash_mismatch",
    "missing_original",
    "unreadable_pdf",
    "encrypted_pdf",
    "too_many_pages",
    "timeout",
    "retries_exhausted",
    "extraction_failed",
    "statement_multiple_accounts",
    "statement_foreign_currency",
    "statement_no_table",
)
CLASSIFICATIONS = ("payslip", "bank_statement", "unrecognised")
PAYSLIP_FIELD_CODES = (
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
STATEMENT_FIELD_CODES = (
    "account_ending",
    "statement_period_start",
    "statement_period_end",
    "opening_balance",
    "closing_balance",
    "total_paid_in",
    "total_paid_out",
)
KINDS = ("transaction", "brought_forward", "carried_forward", "unparsed")


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _replace_check(table: str, name: str, sql: str) -> None:
    op.drop_constraint(op.f(f"ck_{table}_{name}"), table, type_="check")
    op.create_check_constraint(name, table, sql)


def upgrade() -> None:
    _replace_check("documents", "document_type", _in("document_type", DOCUMENT_TYPES))
    _replace_check(
        "documents", "failure_code", f"failure_code IS NULL OR {_in('failure_code', FAILURE_CODES)}"
    )
    _replace_check("extraction_runs", "classification", _in("classification", CLASSIFICATIONS))
    op.add_column("extraction_runs", sa.Column("account_fingerprint", sa.String(64), nullable=True))
    op.create_check_constraint(
        "account_fingerprint",
        "extraction_runs",
        "account_fingerprint IS NULL OR account_fingerprint ~ '^[0-9a-f]{64}$'",
    )
    _replace_check(
        "extracted_fields",
        "field_code",
        f"field_code IS NULL OR {_in('field_code', PAYSLIP_FIELD_CODES + STATEMENT_FIELD_CODES)}",
    )

    op.create_table(
        "extracted_transactions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("row_number", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("posted_on", sa.Date(), nullable=True),
        sa.Column("date_inherited", sa.Boolean(), nullable=False),
        sa.Column("description", sa.String(500), nullable=False),
        sa.Column("description_truncated", sa.Boolean(), nullable=False),
        sa.Column("paid_in_minor", sa.BigInteger(), nullable=True),
        sa.Column("paid_out_minor", sa.BigInteger(), nullable=True),
        sa.Column("balance_minor", sa.BigInteger(), nullable=True),
        sa.Column("cells", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("page_width", sa.Float(), nullable=False),
        sa.Column("page_height", sa.Float(), nullable=False),
        sa.Column("unit", sa.String(8), nullable=False),
        sa.Column("x0", sa.Float(), nullable=False),
        sa.Column("y0", sa.Float(), nullable=False),
        sa.Column("x1", sa.Float(), nullable=False),
        sa.Column("y1", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_extracted_transactions")),
        sa.UniqueConstraint(
            "run_id", "row_number", name=op.f("uq_extracted_transactions_run_id_row_number")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_extracted_transactions_tenant_id_tenants"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "case_id", "run_id"],
            ["extraction_runs.tenant_id", "extraction_runs.case_id", "extraction_runs.id"],
            name=op.f("fk_extracted_transactions_run"),
        ),
        sa.CheckConstraint(_in("kind", KINDS), name="kind"),
        sa.CheckConstraint("row_number > 0", name="row_number"),
        sa.CheckConstraint(
            "(paid_in_minor IS NULL OR paid_in_minor >= 0) "
            "AND (paid_out_minor IS NULL OR paid_out_minor >= 0)",
            name="amounts_not_negative",
        ),
        sa.CheckConstraint(
            "kind <> 'transaction' OR num_nonnulls(paid_in_minor, paid_out_minor) >= 1",
            name="transaction_has_amount",
        ),
        sa.CheckConstraint(
            "kind = 'transaction' OR (paid_in_minor IS NULL AND paid_out_minor IS NULL)",
            name="only_transactions_have_amounts",
        ),
        sa.CheckConstraint(
            "kind <> 'unparsed' OR balance_minor IS NULL", name="unparsed_has_no_balance"
        ),
        sa.CheckConstraint("jsonb_typeof(cells) = 'object'", name="cells"),
        sa.CheckConstraint("page_number >= 1 AND page_width > 0 AND page_height > 0", name="page"),
        sa.CheckConstraint("unit IN ('pt', 'inch')", name="unit"),
        sa.CheckConstraint(
            "0 <= x0 AND x0 <= x1 AND x1 <= page_width AND 0 <= y0 AND y0 <= y1 "
            "AND y1 <= page_height",
            name="row_box",
        ),
    )
    for column in ("tenant_id", "run_id"):
        op.create_index(
            op.f(f"ix_extracted_transactions_{column}"), "extracted_transactions", [column]
        )
    op.execute("ALTER TABLE extracted_transactions ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON extracted_transactions "
        "USING (tenant_id = app_current_tenant()) "
        "WITH CHECK (tenant_id = app_current_tenant())"
    )
    op.execute(f"GRANT SELECT, INSERT ON extracted_transactions TO {APP_ROLE}")


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM documents WHERE document_type = 'bank_statement')
             OR EXISTS (SELECT 1 FROM extraction_runs WHERE classification = 'bank_statement')
          THEN
            RAISE EXCEPTION 'Bank statements exist; downgrading 0017 would discard them';
          END IF;
        END $$
        """
    )
    op.drop_table("extracted_transactions")
    _replace_check(
        "extracted_fields",
        "field_code",
        f"field_code IS NULL OR {_in('field_code', PAYSLIP_FIELD_CODES)}",
    )
    op.drop_constraint(
        op.f("ck_extraction_runs_account_fingerprint"), "extraction_runs", type_="check"
    )
    op.drop_column("extraction_runs", "account_fingerprint")
    _replace_check(
        "extraction_runs", "classification", _in("classification", ("payslip", "unrecognised"))
    )
    _replace_check(
        "documents",
        "failure_code",
        f"failure_code IS NULL OR {_in('failure_code', FAILURE_CODES[:-3])}",
    )
    _replace_check("documents", "document_type", _in("document_type", ("payslip",)))
