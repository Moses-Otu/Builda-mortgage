"""Financial items, their parties, and typed observations.

Three questions are kept apart (see docs/ARCHITECTURE.md):

* **Legal liability** - who owes or guarantees a commitment: ``commitment_parties``.
* **Case association** - the case an item was declared or evidenced in: ``case_id``.
* **Inclusion in reconciliation** - computed per reconciliation run (Phase 7), never stored
  on these tables. Removing an applicant therefore never edits or reassigns their records.

Integrity is enforced in the database: every row is bound to its tenant *and case* through
composite foreign keys, and every observation's value must match its measure's definition.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    Date,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from meip.core.db import Base, check_in, utcnow
from meip.core.ids import uuid7
from meip.financial.measures import (
    CommitmentKind,
    CommitmentStatus,
    EmploymentBasis,
    ExpenditureCategory,
    ExpenditureScope,
    Frequency,
    IncomeKind,
    LiabilityRole,
    RetiredReason,
    ShareBasis,
    SourceKind,
    SubjectKind,
    TemporalKind,
    ValueType,
)

PERCENT_0_100 = "BETWEEN 0 AND 100"


def _applicant_fk(name: str) -> ForeignKeyConstraint:
    """The applicant must belong to the same tenant *and* the same case."""
    return ForeignKeyConstraint(
        ["tenant_id", "case_id", "applicant_id"],
        ["applicants.tenant_id", "applicants.case_id", "applicants.id"],
        name=name,
    )


def _case_fk(name: str) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["tenant_id", "case_id"], ["cases.tenant_id", "cases.id"], name=name
    )


class MeasureDefinition(Base):
    """Reference data (no tenant data): which value and time semantics each measure has."""

    __tablename__ = "measure_definitions"
    __table_args__ = (
        UniqueConstraint("code", "subject_kind", "value_type", "temporal_kind"),
        CheckConstraint(check_in("subject_kind", SubjectKind), name="subject_kind"),
        CheckConstraint(check_in("value_type", ValueType), name="value_type"),
        CheckConstraint(check_in("temporal_kind", TemporalKind), name="temporal_kind"),
    )

    code: Mapped[str] = mapped_column(String(48), primary_key=True)
    subject_kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    value_type: Mapped[str] = mapped_column(String(16))
    temporal_kind: Mapped[str] = mapped_column(String(12))
    description: Mapped[str] = mapped_column(Text)


class IncomeSource(Base):
    """Something an applicant receives income from: an employment, a business, a let, ...

    Pay components (basic, overtime, bonus, ...) are *measures* of one employment, not
    separate sources, because they arrive together on the same payslip.
    """

    __tablename__ = "income_sources"
    __table_args__ = (
        UniqueConstraint("tenant_id", "case_id", "id"),
        _case_fk("fk_income_sources_case"),
        _applicant_fk("fk_income_sources_applicant"),
        CheckConstraint(check_in("kind", IncomeKind), name="kind"),
        CheckConstraint(f"basis IS NULL OR {check_in('basis', EmploymentBasis)}", name="basis"),
        CheckConstraint(
            "end_date IS NULL OR start_date IS NULL OR end_date >= start_date", name="dates"
        ),
        CheckConstraint(
            f"ownership_share IS NULL OR ownership_share {PERCENT_0_100}", name="ownership_share"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    case_id: Mapped[uuid.UUID] = mapped_column(index=True)
    applicant_id: Mapped[uuid.UUID] = mapped_column(index=True)
    kind: Mapped[str] = mapped_column(String(32))
    name: Mapped[str | None] = mapped_column(String(200))
    basis: Mapped[str | None] = mapped_column(String(16))
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    ownership_share: Mapped[Decimal | None] = mapped_column(Numeric(7, 4))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
    version: Mapped[int] = mapped_column(Integer, default=1)

    __mapper_args__ = {"version_id_col": version}  # noqa: RUF012


class Commitment(Base):
    """A financial obligation declared or evidenced in a case. Liability is in its parties."""

    __tablename__ = "commitments"
    __table_args__ = (
        UniqueConstraint("tenant_id", "case_id", "id"),
        _case_fk("fk_commitments_case"),
        CheckConstraint(check_in("kind", CommitmentKind), name="kind"),
        CheckConstraint(check_in("status", CommitmentStatus), name="status"),
        CheckConstraint(
            "account_ref_masked IS NULL OR account_ref_masked ~ '^[A-Za-z0-9]{1,4}$'",
            name="account_ref_masked",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    case_id: Mapped[uuid.UUID] = mapped_column(index=True)
    kind: Mapped[str] = mapped_column(String(32))
    creditor: Mapped[str | None] = mapped_column(String(200))
    account_ref_masked: Mapped[str | None] = mapped_column(String(4))
    status: Mapped[str] = mapped_column(String(16), default=CommitmentStatus.OPEN.value)
    to_be_repaid_before_completion: Mapped[bool | None] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
    version: Mapped[int] = mapped_column(Integer, default=1)

    __mapper_args__ = {"version_id_col": version}  # noqa: RUF012


class CommitmentParty(Base):
    """Legal liability for a commitment. ``responsibility_share`` is an allocation of who
    actually pays, not liability, and is unset until declared or decided by the broker."""

    __tablename__ = "commitment_parties"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "case_id", "commitment_id"],
            ["commitments.tenant_id", "commitments.case_id", "commitments.id"],
            name="fk_commitment_parties_commitment",
        ),
        _applicant_fk("fk_commitment_parties_applicant"),
        CheckConstraint(check_in("liability_role", LiabilityRole), name="liability_role"),
        CheckConstraint(
            f"responsibility_share IS NULL OR responsibility_share {PERCENT_0_100}",
            name="responsibility_share",
        ),
        CheckConstraint(
            f"share_basis IS NULL OR {check_in('share_basis', ShareBasis)}", name="share_basis"
        ),
        CheckConstraint(
            "(responsibility_share IS NULL) = (share_basis IS NULL)", name="share_consistency"
        ),
    )

    commitment_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    applicant_id: Mapped[uuid.UUID] = mapped_column(primary_key=True, index=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    case_id: Mapped[uuid.UUID] = mapped_column()
    liability_role: Mapped[str] = mapped_column(String(16))
    responsibility_share: Mapped[Decimal | None] = mapped_column(Numeric(7, 4))
    share_basis: Mapped[str | None] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class ExpenditureItem(Base):
    __tablename__ = "expenditure_items"
    __table_args__ = (
        UniqueConstraint("tenant_id", "case_id", "id"),
        _case_fk("fk_expenditure_items_case"),
        CheckConstraint(check_in("category", ExpenditureCategory), name="category"),
        CheckConstraint(check_in("scope", ExpenditureScope), name="scope"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    case_id: Mapped[uuid.UUID] = mapped_column(index=True)
    category: Mapped[str] = mapped_column(String(32))
    scope: Mapped[str] = mapped_column(String(16))
    description: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
    version: Mapped[int] = mapped_column(Integer, default=1)

    __mapper_args__ = {"version_id_col": version}  # noqa: RUF012


class ExpenditureParty(Base):
    """Who bears individual expenditure (``share`` optional). Household items have none."""

    __tablename__ = "expenditure_parties"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "case_id", "expenditure_item_id"],
            ["expenditure_items.tenant_id", "expenditure_items.case_id", "expenditure_items.id"],
            name="fk_expenditure_parties_item",
        ),
        _applicant_fk("fk_expenditure_parties_applicant"),
        CheckConstraint(f"share IS NULL OR share {PERCENT_0_100}", name="share"),
    )

    expenditure_item_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    applicant_id: Mapped[uuid.UUID] = mapped_column(primary_key=True, index=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    case_id: Mapped[uuid.UUID] = mapped_column()
    share: Mapped[Decimal | None] = mapped_column(Numeric(7, 4))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


SUBJECT_KIND_EXPRESSION = (
    "CASE WHEN income_source_id IS NOT NULL THEN 'income_source'"
    " WHEN commitment_id IS NOT NULL THEN 'commitment'"
    " WHEN expenditure_item_id IS NOT NULL THEN 'expenditure' END"
)

# Each value type populates exactly its own column(s), within its own range.
VALUE_CHECKS: dict[str, str] = {
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
    # Time semantics follow the measure's temporal kind.
    "temporal": (
        "(temporal_kind = 'flow' AND frequency IS NOT NULL AND as_of_date IS NULL)"
        " OR (temporal_kind = 'stock' AND frequency IS NULL AND as_of_date IS NOT NULL)"
        " OR (temporal_kind = 'attribute' AND frequency IS NULL AND as_of_date IS NULL"
        " AND period_start IS NULL AND period_end IS NULL AND tax_year IS NULL)"
    ),
    "period": "period_end IS NULL OR period_start IS NULL OR period_end >= period_start",
    "tax_year": "tax_year IS NULL OR tax_year ~ '^[0-9]{4}-[0-9]{2}$'",
    "retirement": "(retired_at IS NULL) = (retired_reason IS NULL)",
}


class FinancialObservation(Base):
    """One immutable, typed statement about an item, from one source.

    Values are never edited: a correction is a new observation that supersedes the old one,
    which is retired (the runtime role may update only the retirement columns).
    """

    __tablename__ = "financial_observations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "case_id", "id"),
        _case_fk("fk_financial_observations_case"),
        ForeignKeyConstraint(
            ["tenant_id", "case_id", "income_source_id"],
            ["income_sources.tenant_id", "income_sources.case_id", "income_sources.id"],
            name="fk_financial_observations_income_source",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "case_id", "commitment_id"],
            ["commitments.tenant_id", "commitments.case_id", "commitments.id"],
            name="fk_financial_observations_commitment",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "case_id", "expenditure_item_id"],
            ["expenditure_items.tenant_id", "expenditure_items.case_id", "expenditure_items.id"],
            name="fk_financial_observations_expenditure_item",
        ),
        # The measure fixes the subject, value type and temporal kind.
        ForeignKeyConstraint(
            ["measure", "subject_kind", "value_type", "temporal_kind"],
            [
                "measure_definitions.code",
                "measure_definitions.subject_kind",
                "measure_definitions.value_type",
                "measure_definitions.temporal_kind",
            ],
            name="fk_financial_observations_measure",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "case_id", "supersedes_observation_id"],
            [
                "financial_observations.tenant_id",
                "financial_observations.case_id",
                "financial_observations.id",
            ],
            name="fk_financial_observations_supersedes",
        ),
        # A value can be corrected once: at most one observation supersedes any other.
        Index(
            "uq_financial_observations_supersedes",
            "supersedes_observation_id",
            unique=True,
            postgresql_where=text("supersedes_observation_id IS NOT NULL"),
        ),
        *(CheckConstraint(sql, name=name) for name, sql in VALUE_CHECKS.items()),
        CheckConstraint(check_in("value_type", ValueType), name="value_type"),
        CheckConstraint(
            f"frequency IS NULL OR {check_in('frequency', Frequency)}", name="frequency"
        ),
        CheckConstraint(check_in("source_kind", SourceKind), name="source_kind"),
        CheckConstraint(
            f"retired_reason IS NULL OR {check_in('retired_reason', RetiredReason)}",
            name="retired_reason",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    case_id: Mapped[uuid.UUID] = mapped_column(index=True)
    income_source_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    commitment_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    expenditure_item_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    subject_kind: Mapped[str] = mapped_column(
        String(16), Computed(SUBJECT_KIND_EXPRESSION, persisted=True)
    )
    measure: Mapped[str] = mapped_column(String(48))
    value_type: Mapped[str] = mapped_column(String(16))
    temporal_kind: Mapped[str] = mapped_column(String(12))
    money_minor: Mapped[int | None] = mapped_column(BigInteger)
    currency: Mapped[str | None] = mapped_column(String(3))
    percent: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    duration_months: Mapped[int | None] = mapped_column(Integer)
    count_value: Mapped[int | None] = mapped_column(Integer)
    date_value: Mapped[date | None] = mapped_column(Date)
    frequency: Mapped[str | None] = mapped_column(String(16))
    period_start: Mapped[date | None] = mapped_column(Date)
    period_end: Mapped[date | None] = mapped_column(Date)
    tax_year: Mapped[str | None] = mapped_column(String(7))
    as_of_date: Mapped[date | None] = mapped_column(Date)
    source_kind: Mapped[str] = mapped_column(String(16))
    # Convenience only; the audit log is the authoritative record of who recorded it (D11).
    recorded_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    recorded_at: Mapped[datetime] = mapped_column(default=utcnow)
    supersedes_observation_id: Mapped[uuid.UUID | None] = mapped_column()
    retired_at: Mapped[datetime | None] = mapped_column()
    retired_reason: Mapped[str | None] = mapped_column(String(32))

    # Fetch the database-computed subject_kind with RETURNING after INSERT *and* UPDATE;
    # otherwise it is expired and would need a lazy load, which async sessions cannot do.
    __mapper_args__ = {"eager_defaults": True}  # noqa: RUF012
