"""Vocabulary of the financial domain: item kinds, value types and measures.

``MEASURES`` is the single source for the ``measure_definitions`` reference table (seeded
by migration). Each measure is fixed to one subject kind, one value type and one temporal
kind; the database enforces that every observation matches its definition.

Adding a measure is a reviewed migration, never runtime data.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


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
    # Phase 2 adds 'document' (normalised from extracted evidence, with lineage).


class RetiredReason(StrEnum):
    SUPERSEDED = "superseded"
    ENTERED_IN_ERROR = "entered_in_error"


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


_I, _C, _E = SubjectKind.INCOME_SOURCE, SubjectKind.COMMITMENT, SubjectKind.EXPENDITURE
_M, _SM, _R, _P = ValueType.MONEY, ValueType.SIGNED_MONEY, ValueType.RATE, ValueType.PROPORTION
_D, _N, _DT = ValueType.DURATION, ValueType.COUNT, ValueType.DATE
_FLOW, _STOCK, _ATTR = TemporalKind.FLOW, TemporalKind.STOCK, TemporalKind.ATTRIBUTE

MEASURES: tuple[Measure, ...] = (
    # --- income: pay components and deductions (employment, directorship) ---------------
    Measure("basic_pay", _I, _M, _FLOW, "Basic pay before deductions"),
    Measure("overtime", _I, _M, _FLOW, "Overtime pay"),
    Measure("bonus", _I, _M, _FLOW, "Bonus"),
    Measure("commission", _I, _M, _FLOW, "Commission"),
    Measure("allowance", _I, _M, _FLOW, "Allowances (e.g. shift, car, location)"),
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
