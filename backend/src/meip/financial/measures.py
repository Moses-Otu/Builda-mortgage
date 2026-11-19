"""Vocabulary of the financial domain: item kinds, value types, measures and qualifiers.

``MEASURES`` and ``QUALIFIERS`` are the single source for the ``measure_definitions`` and
``measure_qualifiers`` reference tables (seeded by migration). Each measure is fixed to one
subject kind, one value type and one temporal kind; the database enforces that every
observation matches its definition.

**Meaning is immutable.** A measure or qualifier code means what its description said when
it was introduced, for ever. The database lets a definition be *added* or *deprecated*
(once), never edited or deleted. Deprecation only stops new values; it never changes how
existing values are read. A new or refined meaning needs a new code, in a reviewed migration.

**Qualifiers are a single label, not a classification.** For ``allowance`` and ``bonus`` the
qualifier records the one label the evidence or the declarant gives. The bonus labels overlap
conceptually (a performance bonus may also be guaranteed or discretionary), so no rule may
treat qualifiers as exhaustive or mutually exclusive. Further dimensions (for example
contractual basis versus trigger) are to be added as separate, named attributes in a later
migration, never packed into ``qualifier``.

**Applicability is application-enforced.** ``MEASURE_APPLICABILITY`` says which item kinds a
measure may describe. Observations do not store their item's kind, so the database cannot
check this: every write path must call :func:`ensure_applicable` (a static test enforces it).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from meip.core.errors import BusinessRuleError


class SubjectKind(StrEnum):
    INCOME_SOURCE = "income_source"
    COMMITMENT = "commitment"
    EXPENDITURE = "expenditure"


class ValueType(StrEnum):
    MONEY = "money"
    """Non-negative amount in minor units (pence)."""
    SIGNED_MONEY = "signed_money"
    """Amount that may be negative, e.g. a self-employed loss."""
    RATE = "rate"
    """A percentage >= 0 with no upper bound, e.g. an interest rate or APR."""
    PROPORTION = "proportion"
    """A percentage between 0 and 100, e.g. an ownership share."""
    DURATION = "duration"
    """Whole months."""
    COUNT = "count"
    DATE = "date"


class TemporalKind(StrEnum):
    FLOW = "flow"
    """An amount per period: requires a frequency."""
    STOCK = "stock"
    """A value at a point in time: requires an as-of date."""
    ATTRIBUTE = "attribute"
    """A fixed property: neither frequency nor as-of date."""


class Frequency(StrEnum):
    ONE_OFF = "one_off"
    WEEKLY = "weekly"
    FORTNIGHTLY = "fortnightly"
    FOUR_WEEKLY = "four_weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    ANNUAL = "annual"


class SourceKind(StrEnum):
    DECLARED = "declared"
    """What the applicant says."""
    BROKER_ENTRY = "broker_entry"
    """Entered by the broker, e.g. from a conversation or a document read manually."""
    DOCUMENT = "document"
    """Saved from a broker-reviewed document; linked to its review decision. Created only
    through a document review, never through the observation API."""


class RetiredReason(StrEnum):
    SUPERSEDED = "superseded"
    ENTERED_IN_ERROR = "entered_in_error"
    ITEM_WITHDRAWN = "item_withdrawn"
    """The item itself was withdrawn; its values went with it."""


class WithdrawalReason(StrEnum):
    """Why an item should never have existed. Ending (a settled loan, a past job) is not
    withdrawal: those stay true records."""

    ENTERED_IN_ERROR = "entered_in_error"
    DUPLICATE = "duplicate"


class IncomeKind(StrEnum):
    EMPLOYMENT = "employment"
    SELF_EMPLOYMENT = "self_employment"
    DIRECTORSHIP = "directorship"
    PROPERTY_RENTAL = "property_rental"
    PENSION = "pension"
    BENEFIT = "benefit"
    INVESTMENT = "investment"
    MAINTENANCE_RECEIVED = "maintenance_received"
    OTHER = "other"


class EmploymentBasis(StrEnum):
    PERMANENT = "permanent"
    FIXED_TERM = "fixed_term"
    CONTRACT = "contract"
    ZERO_HOURS = "zero_hours"
    AGENCY = "agency"
    OTHER = "other"


class CommitmentKind(StrEnum):
    CREDIT_CARD = "credit_card"
    PERSONAL_LOAN = "personal_loan"
    CAR_FINANCE = "car_finance"
    STUDENT_LOAN = "student_loan"
    MORTGAGE = "mortgage"
    SECURED_LOAN = "secured_loan"
    OVERDRAFT = "overdraft"
    BUY_NOW_PAY_LATER = "buy_now_pay_later"
    MAINTENANCE_PAYABLE = "maintenance_payable"
    OTHER = "other"


class CommitmentStatus(StrEnum):
    OPEN = "open"
    SETTLED = "settled"


class LiabilityRole(StrEnum):
    PRINCIPAL = "principal"
    """Legally liable for the debt (alone, or jointly with other principals)."""
    GUARANTOR = "guarantor"


class ShareBasis(StrEnum):
    DECLARED = "declared"
    BROKER = "broker"


class ExpenditureCategory(StrEnum):
    HOUSING_RENT = "housing_rent"
    COUNCIL_TAX = "council_tax"
    UTILITIES = "utilities"
    TRANSPORT = "transport"
    CHILDCARE = "childcare"
    INSURANCE = "insurance"
    SERVICE_CHARGE_GROUND_RENT = "service_charge_ground_rent"
    OTHER = "other"


class ExpenditureScope(StrEnum):
    HOUSEHOLD = "household"
    """Shared by the household; no parties."""
    INDIVIDUAL = "individual"
    """Borne by specific applicants, recorded as parties."""


@dataclass(frozen=True)
class Measure:
    code: str
    subject_kind: SubjectKind
    value_type: ValueType
    temporal_kind: TemporalKind
    description: str
    qualifier_required: bool = False


@dataclass(frozen=True)
class Qualifier:
    measure: str
    subject_kind: SubjectKind
    code: str
    description: str


_I, _C, _E = SubjectKind.INCOME_SOURCE, SubjectKind.COMMITMENT, SubjectKind.EXPENDITURE
_M, _SM, _R, _P = ValueType.MONEY, ValueType.SIGNED_MONEY, ValueType.RATE, ValueType.PROPORTION
_D, _N, _DT = ValueType.DURATION, ValueType.COUNT, ValueType.DATE
_FLOW, _STOCK, _ATTR = TemporalKind.FLOW, TemporalKind.STOCK, TemporalKind.ATTRIBUTE

MEASURES: tuple[Measure, ...] = (
    # --- income: pay components and deductions (employment, directorship) ---------------
    Measure("basic_pay", _I, _M, _FLOW, "Basic pay before deductions"),
    Measure("overtime", _I, _M, _FLOW, "Overtime pay, including premium and enhanced rates"),
    Measure("bonus", _I, _M, _FLOW, "Bonus", qualifier_required=True),
    Measure("commission", _I, _M, _FLOW, "Commission"),
    Measure(
        "allowance",
        _I,
        _M,
        _FLOW,
        "Allowances (e.g. shift, car, location)",
        qualifier_required=True,
    ),
    Measure("gross_pay", _I, _M, _FLOW, "Total gross pay for the period"),
    Measure("net_pay", _I, _M, _FLOW, "Net pay received"),
    Measure("income_tax", _I, _M, _FLOW, "Income tax deducted"),
    Measure("national_insurance", _I, _M, _FLOW, "National Insurance deducted"),
    Measure("pension_contribution", _I, _M, _FLOW, "Employee pension contribution"),
    Measure("student_loan_deduction", _I, _M, _FLOW, "Student loan repayment via payroll"),
    Measure("ytd_gross_pay", _I, _M, _STOCK, "Gross pay year to date"),
    Measure("ytd_income_tax", _I, _M, _STOCK, "Income tax year to date"),
    Measure("pension_contribution_rate", _I, _P, _ATTR, "Employee pension contribution %"),
    Measure("contract_end_date", _I, _DT, _ATTR, "End date of a fixed-term contract"),
    Measure("probation_end_date", _I, _DT, _ATTR, "End of probationary period"),
    # --- income: self-employment, directorship, other sources ----------------------------
    Measure("turnover", _I, _M, _FLOW, "Business turnover"),
    Measure("net_profit", _I, _SM, _FLOW, "Net profit (negative for a loss)"),
    Measure("dividends", _I, _M, _FLOW, "Dividends received"),
    Measure("share_of_net_profit", _I, _SM, _FLOW, "Director's share of company net profit"),
    Measure("gross_rent", _I, _M, _FLOW, "Gross rental income"),
    Measure("rental_costs", _I, _M, _FLOW, "Costs of letting"),
    Measure("pension_income", _I, _M, _FLOW, "Pension income"),
    Measure("benefit_amount", _I, _M, _FLOW, "Benefit received"),
    Measure("maintenance_amount", _I, _M, _FLOW, "Maintenance received"),
    Measure("investment_income", _I, _M, _FLOW, "Investment income"),
    Measure("total_gross_income", _I, _M, _FLOW, "Total gross income as a single figure"),
    # --- commitments ----------------------------------------------------------------------
    Measure("monthly_payment", _C, _M, _FLOW, "Regular payment"),
    Measure("outstanding_balance", _C, _M, _STOCK, "Amount owed"),
    Measure("credit_limit", _C, _M, _STOCK, "Credit limit"),
    Measure("interest_rate", _C, _R, _ATTR, "Interest rate or APR, %"),
    Measure("remaining_term", _C, _D, _STOCK, "Remaining term in months"),
    Measure("payments_remaining", _C, _N, _STOCK, "Number of payments remaining"),
    Measure("final_payment_date", _C, _DT, _ATTR, "Date of the final payment"),
    # --- expenditure ---------------------------------------------------------------------
    Measure("amount", _E, _M, _FLOW, "Amount spent"),
)

MEASURE_INDEX: dict[tuple[str, SubjectKind], Measure] = {
    (m.code, m.subject_kind): m for m in MEASURES
}

_UNSPECIFIED = "Not stated by the source"
_OTHER = "A known type not listed here"

# Overtime premium is deliberately not an allowance: premium and enhanced overtime rates are
# part of the separate 'overtime' measure, so recording them here would double count.
QUALIFIERS: tuple[Qualifier, ...] = (
    Qualifier("allowance", _I, "car", "Car or car cash allowance"),
    Qualifier("allowance", _I, "shift", "Shift or unsocial hours allowance"),
    Qualifier("allowance", _I, "location", "Location or area weighting"),
    Qualifier("allowance", _I, "travel", "Travel allowance"),
    Qualifier("allowance", _I, "other", _OTHER),
    Qualifier("allowance", _I, "unspecified", _UNSPECIFIED),
    # These labels can overlap: record the one the source uses.
    Qualifier("bonus", _I, "guaranteed", "Described as guaranteed or contractual"),
    Qualifier("bonus", _I, "discretionary", "Described as discretionary"),
    Qualifier("bonus", _I, "performance", "Described as performance-related"),
    Qualifier("bonus", _I, "other", _OTHER),
    Qualifier("bonus", _I, "unspecified", _UNSPECIFIED),
)
QUALIFIER_INDEX: dict[tuple[str, SubjectKind], frozenset[str]] = {
    (m.code, m.subject_kind): frozenset(
        q.code for q in QUALIFIERS if (q.measure, q.subject_kind) == (m.code, m.subject_kind)
    )
    for m in MEASURES
    if m.qualifier_required
}

# --- applicability (application-enforced) -----------------------------------------------

_PAYROLL = frozenset({IncomeKind.EMPLOYMENT, IncomeKind.DIRECTORSHIP})
_TAXED = _PAYROLL | {IncomeKind.SELF_EMPLOYMENT, IncomeKind.PENSION}
_INSTALMENT = frozenset(
    {
        CommitmentKind.PERSONAL_LOAN,
        CommitmentKind.CAR_FINANCE,
        CommitmentKind.MORTGAGE,
        CommitmentKind.SECURED_LOAN,
        CommitmentKind.BUY_NOW_PAY_LATER,
    }
)
_ALL_COMMITMENTS = frozenset(CommitmentKind)

# measure -> item kinds it may describe. 'other' income and commitment kinds accept every
# measure of their subject kind; expenditure has one measure that suits every category.
MEASURE_APPLICABILITY: dict[tuple[str, SubjectKind], frozenset[str]] = {
    **{
        (code, _I): frozenset(_PAYROLL)
        for code in (
            "basic_pay",
            "overtime",
            "bonus",
            "commission",
            "allowance",
            "gross_pay",
            "net_pay",
            "pension_contribution",
            "student_loan_deduction",
            "ytd_gross_pay",
            "ytd_income_tax",
            "pension_contribution_rate",
            "probation_end_date",
        )
    },
    ("income_tax", _I): frozenset(_TAXED),
    ("national_insurance", _I): frozenset(_PAYROLL | {IncomeKind.SELF_EMPLOYMENT}),
    ("contract_end_date", _I): frozenset({IncomeKind.EMPLOYMENT}),
    ("turnover", _I): frozenset({IncomeKind.SELF_EMPLOYMENT, IncomeKind.DIRECTORSHIP}),
    ("net_profit", _I): frozenset({IncomeKind.SELF_EMPLOYMENT, IncomeKind.PROPERTY_RENTAL}),
    ("dividends", _I): frozenset({IncomeKind.DIRECTORSHIP, IncomeKind.INVESTMENT}),
    ("share_of_net_profit", _I): frozenset({IncomeKind.DIRECTORSHIP}),
    ("gross_rent", _I): frozenset({IncomeKind.PROPERTY_RENTAL}),
    ("rental_costs", _I): frozenset({IncomeKind.PROPERTY_RENTAL}),
    ("pension_income", _I): frozenset({IncomeKind.PENSION}),
    ("benefit_amount", _I): frozenset({IncomeKind.BENEFIT}),
    ("maintenance_amount", _I): frozenset({IncomeKind.MAINTENANCE_RECEIVED}),
    ("investment_income", _I): frozenset({IncomeKind.INVESTMENT}),
    ("total_gross_income", _I): frozenset(IncomeKind),
    ("monthly_payment", _C): _ALL_COMMITMENTS,
    ("outstanding_balance", _C): _ALL_COMMITMENTS,
    ("credit_limit", _C): frozenset(
        {CommitmentKind.CREDIT_CARD, CommitmentKind.OVERDRAFT, CommitmentKind.BUY_NOW_PAY_LATER}
    ),
    ("interest_rate", _C): _ALL_COMMITMENTS - {CommitmentKind.MAINTENANCE_PAYABLE},
    ("remaining_term", _C): _INSTALMENT,
    ("payments_remaining", _C): _INSTALMENT,
    ("final_payment_date", _C): _INSTALMENT,
    ("amount", _E): frozenset(ExpenditureCategory),
}
_CATCH_ALL = {_I: IncomeKind.OTHER.value, _C: CommitmentKind.OTHER.value}


def ensure_applicable(measure: str, subject_kind: SubjectKind, item_kind: str) -> None:
    """The single validation path for "may this measure describe this kind of item?".

    Application-enforced, not a database guarantee: every write path (recording a value,
    changing an item's kind, and the Phase 2 normaliser) must call this function.
    """
    if _CATCH_ALL.get(subject_kind) == item_kind:
        return
    allowed = MEASURE_APPLICABILITY.get((measure, subject_kind), frozenset())
    if item_kind not in allowed:
        raise BusinessRuleError(f"'{measure}' does not apply to a '{item_kind}' item")
