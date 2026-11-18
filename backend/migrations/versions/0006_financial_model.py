"""Phase 1.1-E: typed financial items and observations.

* ``measure_definitions`` (reference data, no tenant data): fixes each measure's subject
  kind, value type and temporal kind. Seeded below.
* Items: ``income_sources`` (per applicant), ``commitments`` + ``commitment_parties`` (legal
  liability, with an optional responsibility allocation), ``expenditure_items`` +
  ``expenditure_parties`` (household items have no parties).
* ``financial_observations``: one immutable typed value per row. A composite FK to
  ``measure_definitions`` plus CHECKs make it impossible to store a value of the wrong type,
  with the wrong time semantics, or for the wrong kind of item. The runtime role may update
  only the retirement columns, and a trigger stops a retirement being undone.
* Every financial row is bound to its tenant and case by composite foreign keys, including
  the applicant references (new ``UNIQUE (tenant_id, case_id, id)`` on applicants).

Data: the old ``applicants.declared_*`` figures become a declared income source /
commitment with one declared observation each; then those columns are dropped.

This migration is self-contained (it does not import application code), so later code
changes cannot alter what it does. ``tests/integration/test_schema.py`` checks the seeded
measures against ``meip.financial.measures``.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "meip_app"
_TS = sa.DateTime(timezone=True)


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _nullable_in(column: str, values: Sequence[str]) -> str:
    return f"{column} IS NULL OR {_in(column, values)}"


SUBJECT_KINDS = ("income_source", "commitment", "expenditure")
VALUE_TYPES = ("money", "signed_money", "rate", "proportion", "duration", "count", "date")
TEMPORAL_KINDS = ("flow", "stock", "attribute")
FREQUENCIES = ("one_off", "weekly", "fortnightly", "four_weekly", "monthly", "quarterly", "annual")
INCOME_KINDS = (
    "employment",
    "self_employment",
    "directorship",
    "property_rental",
    "pension",
    "benefit",
    "investment",
    "maintenance_received",
    "other",
)
BASES = ("permanent", "fixed_term", "contract", "zero_hours", "agency", "other")
COMMITMENT_KINDS = (
    "credit_card",
    "personal_loan",
    "car_finance",
    "student_loan",
    "mortgage",
    "secured_loan",
    "overdraft",
    "buy_now_pay_later",
    "maintenance_payable",
    "other",
)
EXPENDITURE_CATEGORIES = (
    "housing_rent",
    "council_tax",
    "utilities",
    "transport",
    "childcare",
    "insurance",
    "service_charge_ground_rent",
    "other",
)

# (code, subject_kind, value_type, temporal_kind, description) - frozen copy of the seed.
MEASURES: tuple[tuple[str, str, str, str, str], ...] = (
    ("basic_pay", "income_source", "money", "flow", "Basic pay before deductions"),
    ("overtime", "income_source", "money", "flow", "Overtime pay"),
    ("bonus", "income_source", "money", "flow", "Bonus"),
    ("commission", "income_source", "money", "flow", "Commission"),
    ("allowance", "income_source", "money", "flow", "Allowances (e.g. shift, car, location)"),
    ("gross_pay", "income_source", "money", "flow", "Total gross pay for the period"),
    ("net_pay", "income_source", "money", "flow", "Net pay received"),
    ("income_tax", "income_source", "money", "flow", "Income tax deducted"),
    ("national_insurance", "income_source", "money", "flow", "National Insurance deducted"),
    ("pension_contribution", "income_source", "money", "flow", "Employee pension contribution"),
    (
        "student_loan_deduction",
        "income_source",
        "money",
        "flow",
        "Student loan repayment via payroll",
    ),
    ("ytd_gross_pay", "income_source", "money", "stock", "Gross pay year to date"),
    ("ytd_income_tax", "income_source", "money", "stock", "Income tax year to date"),
    (
        "pension_contribution_rate",
        "income_source",
        "proportion",
        "attribute",
        "Employee pension contribution %",
    ),
    (
        "contract_end_date",
        "income_source",
        "date",
        "attribute",
        "End date of a fixed-term contract",
    ),
    ("probation_end_date", "income_source", "date", "attribute", "End of probationary period"),
    ("turnover", "income_source", "money", "flow", "Business turnover"),
    ("net_profit", "income_source", "signed_money", "flow", "Net profit (negative for a loss)"),
    ("dividends", "income_source", "money", "flow", "Dividends received"),
    (
        "share_of_net_profit",
        "income_source",
        "signed_money",
        "flow",
        "Director's share of company net profit",
    ),
    ("gross_rent", "income_source", "money", "flow", "Gross rental income"),
    ("rental_costs", "income_source", "money", "flow", "Costs of letting"),
    ("pension_income", "income_source", "money", "flow", "Pension income"),
    ("benefit_amount", "income_source", "money", "flow", "Benefit received"),
    ("maintenance_amount", "income_source", "money", "flow", "Maintenance received"),
    ("investment_income", "income_source", "money", "flow", "Investment income"),
    (
        "total_gross_income",
        "income_source",
        "money",
        "flow",
        "Total gross income as a single figure",
    ),
    ("monthly_payment", "commitment", "money", "flow", "Regular payment"),
    ("outstanding_balance", "commitment", "money", "stock", "Amount owed"),
    ("credit_limit", "commitment", "money", "stock", "Credit limit"),
    ("interest_rate", "commitment", "rate", "attribute", "Interest rate or APR, %"),
    ("remaining_term", "commitment", "duration", "stock", "Remaining term in months"),
    ("payments_remaining", "commitment", "count", "stock", "Number of payments remaining"),
    ("final_payment_date", "commitment", "date", "attribute", "Date of the final payment"),
    ("amount", "expenditure", "money", "flow", "Amount spent"),
)

SUBJECT_KIND_EXPRESSION = (
    "CASE WHEN income_source_id IS NOT NULL THEN 'income_source'"
    " WHEN commitment_id IS NOT NULL THEN 'commitment'"
    " WHEN expenditure_item_id IS NOT NULL THEN 'expenditure' END"
)
OBSERVATION_CHECKS: dict[str, str] = {
    "one_subject": "num_nonnulls(income_source_id, commitment_id, expenditure_item_id) = 1",
    "one_value": (
        "num_nonnulls(money_minor, percent, duration_months, count_value, date_value) = 1"
    ),
    "money_value": (
        "(value_type IN ('money', 'signed_money')) = (money_minor IS NOT NULL)"
        " AND (money_minor IS NULL) = (currency IS NULL)"
        " AND (currency IS NULL OR currency = 'GBP')"
        " AND (value_type <> 'money' OR money_minor >= 0)"
    ),
    "percent_value": (
        "(value_type IN ('rate', 'proportion')) = (percent IS NOT NULL)"
        " AND (value_type <> 'rate' OR percent >= 0)"
        " AND (value_type <> 'proportion' OR percent BETWEEN 0 AND 100)"
    ),
    "duration_value": (
        "(value_type = 'duration') = (duration_months IS NOT NULL)"
        " AND (duration_months IS NULL OR duration_months >= 0)"
    ),
    "count_value": (
        "(value_type = 'count') = (count_value IS NOT NULL)"
        " AND (count_value IS NULL OR count_value >= 0)"
    ),
    "date_value": "(value_type = 'date') = (date_value IS NOT NULL)",
    "temporal": (
        "(temporal_kind = 'flow' AND frequency IS NOT NULL AND as_of_date IS NULL)"
        " OR (temporal_kind = 'stock' AND frequency IS NULL AND as_of_date IS NOT NULL)"
        " OR (temporal_kind = 'attribute' AND frequency IS NULL AND as_of_date IS NULL"
        " AND period_start IS NULL AND period_end IS NULL AND tax_year IS NULL)"
    ),
    "period": "period_end IS NULL OR period_start IS NULL OR period_end >= period_start",
    "tax_year": "tax_year IS NULL OR tax_year ~ '^[0-9]{4}-[0-9]{2}$'",
    "retirement": "(retired_at IS NULL) = (retired_reason IS NULL)",
    "value_type": _in("value_type", VALUE_TYPES),
    "frequency": _nullable_in("frequency", FREQUENCIES),
    "source_kind": _in("source_kind", ("declared", "broker_entry")),
    "retired_reason": _nullable_in("retired_reason", ("superseded", "entered_in_error")),
}

TENANT_TABLES = (
    "income_sources",
    "commitments",
    "commitment_parties",
    "expenditure_items",
    "expenditure_parties",
    "financial_observations",
)


def _tenant_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id"], ["tenants.id"], name=op.f(f"fk_{table}_tenant_id_tenants")
    )


def _case_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id", "case_id"], ["cases.tenant_id", "cases.id"], name=op.f(f"fk_{table}_case")
    )


def _applicant_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id", "case_id", "applicant_id"],
        ["applicants.tenant_id", "applicants.case_id", "applicants.id"],
        name=op.f(f"fk_{table}_applicant"),
    )


def _scoped_columns() -> list[sa.Column[object]]:
    return [
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
    ]


def _versioned_columns() -> list[sa.Column[object]]:
    return [
        sa.Column("created_at", _TS, nullable=False),
        sa.Column("updated_at", _TS, nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
    ]


def _index(table: str, *columns: str) -> None:
    for column in columns:
        op.create_index(op.f(f"ix_{table}_{column}"), table, [column])


def upgrade() -> None:
    op.create_unique_constraint(
        op.f("uq_applicants_tenant_id_case_id_id"), "applicants", ["tenant_id", "case_id", "id"]
    )

    # --- reference data -------------------------------------------------------------------
    op.create_table(
        "measure_definitions",
        sa.Column("code", sa.String(48), nullable=False),
        sa.Column("subject_kind", sa.String(16), nullable=False),
        sa.Column("value_type", sa.String(16), nullable=False),
        sa.Column("temporal_kind", sa.String(12), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("code", "subject_kind", name=op.f("pk_measure_definitions")),
        sa.UniqueConstraint(
            "code",
            "subject_kind",
            "value_type",
            "temporal_kind",
            name=op.f("uq_measure_definitions_code_subject_kind_value_type_temporal_kind"),
        ),
        sa.CheckConstraint(_in("subject_kind", SUBJECT_KINDS), name="subject_kind"),
        sa.CheckConstraint(_in("value_type", VALUE_TYPES), name="value_type"),
        sa.CheckConstraint(_in("temporal_kind", TEMPORAL_KINDS), name="temporal_kind"),
    )
    definitions = sa.table(
        "measure_definitions",
        sa.column("code", sa.String),
        sa.column("subject_kind", sa.String),
        sa.column("value_type", sa.String),
        sa.column("temporal_kind", sa.String),
        sa.column("description", sa.Text),
    )
    op.bulk_insert(
        definitions,
        [
            {
                "code": code,
                "subject_kind": subject,
                "value_type": value_type,
                "temporal_kind": temporal,
                "description": description,
            }
            for code, subject, value_type, temporal, description in MEASURES
        ],
    )

    # --- items ------------------------------------------------------------------------------
    op.create_table(
        "income_sources",
        sa.Column("id", sa.Uuid(), nullable=False),
        *_scoped_columns(),
        sa.Column("applicant_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("name", sa.String(200), nullable=True),
        sa.Column("basis", sa.String(16), nullable=True),
        sa.Column("is_primary", sa.Boolean(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("ownership_share", sa.Numeric(7, 4), nullable=True),
        *_versioned_columns(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_income_sources")),
        sa.UniqueConstraint(
            "tenant_id", "case_id", "id", name=op.f("uq_income_sources_tenant_id_case_id_id")
        ),
        _tenant_fk("income_sources"),
        _case_fk("income_sources"),
        _applicant_fk("income_sources"),
        sa.CheckConstraint(_in("kind", INCOME_KINDS), name="kind"),
        sa.CheckConstraint(_nullable_in("basis", BASES), name="basis"),
        sa.CheckConstraint(
            "end_date IS NULL OR start_date IS NULL OR end_date >= start_date", name="dates"
        ),
        sa.CheckConstraint(
            "ownership_share IS NULL OR ownership_share BETWEEN 0 AND 100",
            name="ownership_share",
        ),
    )
    _index("income_sources", "tenant_id", "case_id", "applicant_id")

    op.create_table(
        "commitments",
        sa.Column("id", sa.Uuid(), nullable=False),
        *_scoped_columns(),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("creditor", sa.String(200), nullable=True),
        sa.Column("account_ref_masked", sa.String(4), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("to_be_repaid_before_completion", sa.Boolean(), nullable=True),
        *_versioned_columns(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_commitments")),
        sa.UniqueConstraint(
            "tenant_id", "case_id", "id", name=op.f("uq_commitments_tenant_id_case_id_id")
        ),
        _tenant_fk("commitments"),
        _case_fk("commitments"),
        sa.CheckConstraint(_in("kind", COMMITMENT_KINDS), name="kind"),
        sa.CheckConstraint(_in("status", ("open", "settled")), name="status"),
        sa.CheckConstraint(
            "account_ref_masked IS NULL OR account_ref_masked ~ '^[A-Za-z0-9]{1,4}$'",
            name="account_ref_masked",
        ),
    )
    _index("commitments", "tenant_id", "case_id")

    op.create_table(
        "commitment_parties",
        sa.Column("commitment_id", sa.Uuid(), nullable=False),
        sa.Column("applicant_id", sa.Uuid(), nullable=False),
        *_scoped_columns(),
        sa.Column("liability_role", sa.String(16), nullable=False),
        sa.Column("responsibility_share", sa.Numeric(7, 4), nullable=True),
        sa.Column("share_basis", sa.String(16), nullable=True),
        sa.Column("created_at", _TS, nullable=False),
        sa.PrimaryKeyConstraint(
            "commitment_id", "applicant_id", name=op.f("pk_commitment_parties")
        ),
        _tenant_fk("commitment_parties"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "case_id", "commitment_id"],
            ["commitments.tenant_id", "commitments.case_id", "commitments.id"],
            name=op.f("fk_commitment_parties_commitment"),
        ),
        _applicant_fk("commitment_parties"),
        sa.CheckConstraint(
            _in("liability_role", ("principal", "guarantor")), name="liability_role"
        ),
        sa.CheckConstraint(
            "responsibility_share IS NULL OR responsibility_share BETWEEN 0 AND 100",
            name="responsibility_share",
        ),
        sa.CheckConstraint(_nullable_in("share_basis", ("declared", "broker")), name="share_basis"),
        sa.CheckConstraint(
            "(responsibility_share IS NULL) = (share_basis IS NULL)", name="share_consistency"
        ),
    )
    _index("commitment_parties", "tenant_id", "applicant_id")

    op.create_table(
        "expenditure_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        *_scoped_columns(),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("description", sa.String(200), nullable=True),
        *_versioned_columns(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_expenditure_items")),
        sa.UniqueConstraint(
            "tenant_id", "case_id", "id", name=op.f("uq_expenditure_items_tenant_id_case_id_id")
        ),
        _tenant_fk("expenditure_items"),
        _case_fk("expenditure_items"),
        sa.CheckConstraint(_in("category", EXPENDITURE_CATEGORIES), name="category"),
        sa.CheckConstraint(_in("scope", ("household", "individual")), name="scope"),
    )
    _index("expenditure_items", "tenant_id", "case_id")

    op.create_table(
        "expenditure_parties",
        sa.Column("expenditure_item_id", sa.Uuid(), nullable=False),
        sa.Column("applicant_id", sa.Uuid(), nullable=False),
        *_scoped_columns(),
        sa.Column("share", sa.Numeric(7, 4), nullable=True),
        sa.Column("created_at", _TS, nullable=False),
        sa.PrimaryKeyConstraint(
            "expenditure_item_id", "applicant_id", name=op.f("pk_expenditure_parties")
        ),
        _tenant_fk("expenditure_parties"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "case_id", "expenditure_item_id"],
            ["expenditure_items.tenant_id", "expenditure_items.case_id", "expenditure_items.id"],
            name=op.f("fk_expenditure_parties_item"),
        ),
        _applicant_fk("expenditure_parties"),
        sa.CheckConstraint("share IS NULL OR share BETWEEN 0 AND 100", name="share"),
    )
    _index("expenditure_parties", "tenant_id", "applicant_id")

    # --- observations -----------------------------------------------------------------------
    op.create_table(
        "financial_observations",
        sa.Column("id", sa.Uuid(), nullable=False),
        *_scoped_columns(),
        sa.Column("income_source_id", sa.Uuid(), nullable=True),
        sa.Column("commitment_id", sa.Uuid(), nullable=True),
        sa.Column("expenditure_item_id", sa.Uuid(), nullable=True),
        sa.Column(
            "subject_kind",
            sa.String(16),
            sa.Computed(SUBJECT_KIND_EXPRESSION, persisted=True),
            nullable=True,
        ),
        sa.Column("measure", sa.String(48), nullable=False),
        sa.Column("value_type", sa.String(16), nullable=False),
        sa.Column("temporal_kind", sa.String(12), nullable=False),
        sa.Column("money_minor", sa.BigInteger(), nullable=True),
        sa.Column("currency", sa.String(3), nullable=True),
        sa.Column("percent", sa.Numeric(10, 4), nullable=True),
        sa.Column("duration_months", sa.Integer(), nullable=True),
        sa.Column("count_value", sa.Integer(), nullable=True),
        sa.Column("date_value", sa.Date(), nullable=True),
        sa.Column("frequency", sa.String(16), nullable=True),
        sa.Column("period_start", sa.Date(), nullable=True),
        sa.Column("period_end", sa.Date(), nullable=True),
        sa.Column("tax_year", sa.String(7), nullable=True),
        sa.Column("as_of_date", sa.Date(), nullable=True),
        sa.Column("source_kind", sa.String(16), nullable=False),
        sa.Column("recorded_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("recorded_at", _TS, nullable=False),
        sa.Column("supersedes_observation_id", sa.Uuid(), nullable=True),
        sa.Column("retired_at", _TS, nullable=True),
        sa.Column("retired_reason", sa.String(32), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_financial_observations")),
        sa.UniqueConstraint(
            "tenant_id",
            "case_id",
            "id",
            name=op.f("uq_financial_observations_tenant_id_case_id_id"),
        ),
        _tenant_fk("financial_observations"),
        _case_fk("financial_observations"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "case_id", "income_source_id"],
            ["income_sources.tenant_id", "income_sources.case_id", "income_sources.id"],
            name=op.f("fk_financial_observations_income_source"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "case_id", "commitment_id"],
            ["commitments.tenant_id", "commitments.case_id", "commitments.id"],
            name=op.f("fk_financial_observations_commitment"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "case_id", "expenditure_item_id"],
            ["expenditure_items.tenant_id", "expenditure_items.case_id", "expenditure_items.id"],
            name=op.f("fk_financial_observations_expenditure_item"),
        ),
        sa.ForeignKeyConstraint(
            ["measure", "subject_kind", "value_type", "temporal_kind"],
            [
                "measure_definitions.code",
                "measure_definitions.subject_kind",
                "measure_definitions.value_type",
                "measure_definitions.temporal_kind",
            ],
            name=op.f("fk_financial_observations_measure"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "case_id", "supersedes_observation_id"],
            [
                "financial_observations.tenant_id",
                "financial_observations.case_id",
                "financial_observations.id",
            ],
            name=op.f("fk_financial_observations_supersedes"),
        ),
        sa.ForeignKeyConstraint(
            ["recorded_by_user_id"],
            ["users.id"],
            name=op.f("fk_financial_observations_recorded_by_user_id_users"),
        ),
        *(sa.CheckConstraint(sql, name=name) for name, sql in OBSERVATION_CHECKS.items()),
    )
    _index(
        "financial_observations",
        "tenant_id",
        "case_id",
        "income_source_id",
        "commitment_id",
        "expenditure_item_id",
    )
    # A value can be corrected once: at most one observation supersedes any other.
    op.create_index(
        "uq_financial_observations_supersedes",
        "financial_observations",
        ["supersedes_observation_id"],
        unique=True,
        postgresql_where=sa.text("supersedes_observation_id IS NOT NULL"),
    )
    # A retirement is final; values are already immutable via column-level grants.
    op.execute(
        """
        CREATE FUNCTION financial_observations_retire_once() RETURNS trigger
        LANGUAGE plpgsql AS
        $$ BEGIN
             IF OLD.retired_at IS NOT NULL THEN
               RAISE EXCEPTION 'observation % is already retired', OLD.id
                 USING ERRCODE = 'integrity_constraint_violation';
             END IF;
             RETURN NEW;
           END $$
        """
    )
    op.execute(
        "CREATE TRIGGER financial_observations_retire_once BEFORE UPDATE "
        "ON financial_observations FOR EACH ROW "
        "EXECUTE FUNCTION financial_observations_retire_once()"
    )

    # --- isolation and grants ---------------------------------------------------------------
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            "USING (tenant_id = app_current_tenant()) "
            "WITH CHECK (tenant_id = app_current_tenant())"
        )
    op.execute(f"GRANT SELECT ON measure_definitions TO {APP_ROLE}")
    for table in ("income_sources", "commitments", "expenditure_items"):
        op.execute(f"GRANT SELECT, INSERT, UPDATE ON {table} TO {APP_ROLE}")
    for table in ("commitment_parties", "expenditure_parties"):
        op.execute(f"GRANT SELECT, INSERT, DELETE ON {table} TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT ON financial_observations TO {APP_ROLE}")
    op.execute(f"GRANT UPDATE (retired_at, retired_reason) ON financial_observations TO {APP_ROLE}")

    # --- move declared figures off applicants -----------------------------------------------
    op.execute(
        """
        WITH base AS MATERIALIZED (
          SELECT gen_random_uuid() AS item_id, id AS applicant_id, tenant_id, case_id,
                 declared_gross_annual_income_minor AS amount
          FROM applicants WHERE declared_gross_annual_income_minor IS NOT NULL
        ), items AS (
          INSERT INTO income_sources (id, tenant_id, case_id, applicant_id, kind, name,
                                      is_primary, created_at, updated_at, version)
          SELECT item_id, tenant_id, case_id, applicant_id, 'other', 'Declared total income',
                 false, now(), now(), 1 FROM base
        )
        INSERT INTO financial_observations (id, tenant_id, case_id, income_source_id, measure,
               value_type, temporal_kind, money_minor, currency, frequency, source_kind,
               recorded_at)
        SELECT gen_random_uuid(), tenant_id, case_id, item_id, 'total_gross_income', 'money',
               'flow', amount, 'GBP', 'annual', 'declared', now()
        FROM base
        """
    )
    op.execute(
        """
        WITH base AS MATERIALIZED (
          SELECT gen_random_uuid() AS item_id, id AS applicant_id, tenant_id, case_id,
                 declared_monthly_commitments_minor AS amount
          FROM applicants WHERE declared_monthly_commitments_minor IS NOT NULL
        ), items AS (
          INSERT INTO commitments (id, tenant_id, case_id, kind, creditor, status,
                                   created_at, updated_at, version)
          SELECT item_id, tenant_id, case_id, 'other', 'Declared total commitments', 'open',
                 now(), now(), 1 FROM base
        ), parties AS (
          INSERT INTO commitment_parties (commitment_id, applicant_id, tenant_id, case_id,
                                          liability_role, created_at)
          SELECT item_id, applicant_id, tenant_id, case_id, 'principal', now() FROM base
        )
        INSERT INTO financial_observations (id, tenant_id, case_id, commitment_id, measure,
               value_type, temporal_kind, money_minor, currency, frequency, source_kind,
               recorded_at)
        SELECT gen_random_uuid(), tenant_id, case_id, item_id, 'monthly_payment', 'money',
               'flow', amount, 'GBP', 'monthly', 'declared', now()
        FROM base
        """
    )
    op.drop_constraint(op.f("ck_applicants_declared_income"), "applicants", type_="check")
    op.drop_constraint(op.f("ck_applicants_declared_commitments"), "applicants", type_="check")
    op.drop_column("applicants", "declared_gross_annual_income_minor")
    op.drop_column("applicants", "declared_monthly_commitments_minor")


def downgrade() -> None:
    # Declared figures entered after this migration live only in the financial tables and
    # are not copied back; the old single-figure columns return empty.
    op.add_column("applicants", sa.Column("declared_monthly_commitments_minor", sa.BigInteger()))
    op.add_column("applicants", sa.Column("declared_gross_annual_income_minor", sa.BigInteger()))
    op.create_check_constraint(
        "declared_income",
        "applicants",
        "declared_gross_annual_income_minor IS NULL OR declared_gross_annual_income_minor >= 0",
    )
    op.create_check_constraint(
        "declared_commitments",
        "applicants",
        "declared_monthly_commitments_minor IS NULL OR declared_monthly_commitments_minor >= 0",
    )
    op.drop_table("financial_observations")
    op.execute("DROP FUNCTION IF EXISTS financial_observations_retire_once()")
    op.drop_table("expenditure_parties")
    op.drop_table("expenditure_items")
    op.drop_table("commitment_parties")
    op.drop_table("commitments")
    op.drop_table("income_sources")
    op.drop_table("measure_definitions")
    op.drop_constraint(op.f("uq_applicants_tenant_id_case_id_id"), "applicants", type_="unique")
