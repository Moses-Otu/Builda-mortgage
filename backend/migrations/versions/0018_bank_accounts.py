"""Stage 2-B step 2B-2: broker confirmation of bank statements; accounts and holders.

* ``bank_accounts``: per case, the last 4 digits and the keyed fingerprint of the account
  (never the number or sort code); ``UNIQUE (tenant, case, fingerprint)`` keeps two accounts
  that share their last 4 digits apart. Insert-only, RLS.
* ``bank_account_holders``: the case's applicants who hold the account, as the broker states
  them (one: sole; two or more: joint). Insert-only, RLS.
* ``document_reviews`` becomes either a payslip review (income source and frequency) or a
  statement confirmation (bank account), exactly one shape (CHECK).

The downgrade refuses while any account exists.

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "meip_app"
_TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "bank_accounts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("account_ending", sa.String(4), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=True),
        sa.Column("label", sa.String(100), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", _TS, nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_bank_accounts")),
        sa.UniqueConstraint(
            "tenant_id", "case_id", "id", name=op.f("uq_bank_accounts_tenant_id_case_id_id")
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "case_id",
            "fingerprint",
            name=op.f("uq_bank_accounts_tenant_id_case_id_fingerprint"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_bank_accounts_tenant_id_tenants")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "case_id"],
            ["cases.tenant_id", "cases.id"],
            name=op.f("fk_bank_accounts_case"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_bank_accounts_created_by_user_id_users"),
        ),
        sa.CheckConstraint("account_ending ~ '^[0-9]{4}$'", name="account_ending"),
        sa.CheckConstraint(
            "fingerprint IS NULL OR fingerprint ~ '^[0-9a-f]{64}$'", name="fingerprint"
        ),
    )
    op.create_index(op.f("ix_bank_accounts_tenant_id"), "bank_accounts", ["tenant_id"])

    op.create_table(
        "bank_account_holders",
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("applicant_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("added_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", _TS, nullable=False),
        sa.PrimaryKeyConstraint("account_id", "applicant_id", name=op.f("pk_bank_account_holders")),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_bank_account_holders_tenant_id_tenants"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "case_id", "account_id"],
            ["bank_accounts.tenant_id", "bank_accounts.case_id", "bank_accounts.id"],
            name=op.f("fk_bank_account_holders_account"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "case_id", "applicant_id"],
            ["applicants.tenant_id", "applicants.case_id", "applicants.id"],
            name=op.f("fk_bank_account_holders_applicant"),
        ),
        sa.ForeignKeyConstraint(
            ["added_by_user_id"],
            ["users.id"],
            name=op.f("fk_bank_account_holders_added_by_user_id_users"),
        ),
    )
    op.create_index(
        op.f("ix_bank_account_holders_tenant_id"), "bank_account_holders", ["tenant_id"]
    )

    op.add_column("document_reviews", sa.Column("bank_account_id", sa.Uuid(), nullable=True))
    op.alter_column("document_reviews", "income_source_id", nullable=True)
    op.alter_column("document_reviews", "frequency", nullable=True)
    op.create_foreign_key(
        op.f("fk_document_reviews_bank_account"),
        "document_reviews",
        "bank_accounts",
        ["tenant_id", "case_id", "bank_account_id"],
        ["tenant_id", "case_id", "id"],
    )
    op.create_check_constraint(
        "payslip_or_statement",
        "document_reviews",
        "(income_source_id IS NOT NULL AND frequency IS NOT NULL AND bank_account_id IS NULL)"
        " OR (income_source_id IS NULL AND frequency IS NULL AND bank_account_id IS NOT NULL)",
    )

    for table in ("bank_accounts", "bank_account_holders"):
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
          IF EXISTS (SELECT 1 FROM bank_accounts)
             OR EXISTS (SELECT 1 FROM document_reviews WHERE bank_account_id IS NOT NULL) THEN
            RAISE EXCEPTION 'Bank accounts exist; downgrading 0018 would discard them';
          END IF;
        END $$
        """
    )
    op.drop_constraint(
        op.f("ck_document_reviews_payslip_or_statement"), "document_reviews", type_="check"
    )
    op.drop_constraint(
        op.f("fk_document_reviews_bank_account"), "document_reviews", type_="foreignkey"
    )
    op.alter_column("document_reviews", "frequency", nullable=False)
    op.alter_column("document_reviews", "income_source_id", nullable=False)
    op.drop_column("document_reviews", "bank_account_id")
    op.drop_table("bank_account_holders")
    op.drop_table("bank_accounts")
