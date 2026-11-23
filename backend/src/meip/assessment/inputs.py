"""Assessment inputs: what the profile says, as the policy counts it.

Reads the financial profile exactly as the broker sees it (``profile.service.snapshot``,
unchanged) and derives, per applicant and income source:

* **Monthly figures**, normalised by frequency (weekly x 52/12, four-weekly x 13/12, …).
  One-off amounts are non-recurring and never counted.
* **Verification status**, never upgraded by assumption:
  ``verified`` (broker-reviewed document figures **and** an exact salary credit on a confirmed
  statement), ``extracted`` (broker-reviewed document figures only), ``declared`` (only what
  the applicant or broker stated), ``missing`` (no figures).
* **No double counting:** payroll income uses total gross pay; its components (basic, overtime,
  bonus, commission) are only used to discount variable pay. Declared figures are ignored for
  counting when a document evidences the same source (they are kept for comparison).
* **Unknown stays unknown:** when net income isn't evidenced for a source it is excluded from
  the net-income total (which understates, never overstates, affordability) and listed.

``derive`` is pure (testable without a database); ``load`` gathers its inputs.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select

from meip.applicants.models import Applicant
from meip.assessment.policy import Policy
from meip.profile import service as profile_service
from meip.profile.schemas import FigureOut, ProfileSnapshot
from meip.tenancy.context import OperationContext

MONTHLY: dict[str, Decimal] = {
    "weekly": Decimal(52) / Decimal(12),
    "fortnightly": Decimal(26) / Decimal(12),
    "four_weekly": Decimal(13) / Decimal(12),
    "monthly": Decimal(1),
    "quarterly": Decimal(1) / Decimal(3),
    "annual": Decimal(1) / Decimal(12),
}
PAYROLL = frozenset({"employment", "directorship"})
VARIABLE = ("overtime", "bonus", "commission")
SINGLE_AMOUNT = {
    "pension": "pension_income",
    "benefit": "benefit_amount",
    "maintenance_received": "maintenance_amount",
    "investment": "investment_income",
}
REVOLVING = frozenset({"credit_card", "overdraft"})


def _monthly(amount_minor: int, frequency: str | None) -> int | None:
    factor = MONTHLY.get(frequency or "")
    if factor is None:
        return None  # one-off or unknown: non-recurring, never counted
    return int((Decimal(amount_minor) * factor).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _money(f: FigureOut) -> int | None:
    value = f.value
    return getattr(value, "amount_minor", None)


def _avg(values: list[int]) -> int:
    if not values:
        return 0
    return int((Decimal(sum(values)) / Decimal(len(values))).quantize(Decimal(1), ROUND_HALF_UP))


@dataclass
class IncomeLine:
    """One income source as the assessment counts it."""

    income_source_id: uuid.UUID
    applicant_id: uuid.UUID
    applicant_label: str
    kind: str
    name: str | None
    basis: str | None
    verification: str  # verified | extracted | declared | missing
    recurring: bool
    periods_evidenced: list[str]  # "YYYY-MM" of the document periods used
    gross_monthly_minor: int | None
    net_monthly_minor: int | None
    variable_monthly_minor: int
    qualifying_gross_monthly_minor: int
    qualifying_net_monthly_minor: int | None
    counted: bool
    declared_gross_monthly_minor: int | None
    start_date: date | None
    months_in_employment: int | None
    probation_end: date | None
    contract_end: date | None
    corroborated_periods: list[str]
    figure_ids: list[uuid.UUID]
    notes: list[str] = field(default_factory=list)


@dataclass
class CommitmentLine:
    commitment_id: uuid.UUID
    kind: str
    creditor: str | None
    status: str
    monthly_minor: int | None
    basis: str  # declared | document | assumed | missing
    counted: bool
    outstanding_balance_minor: int | None
    notes: list[str] = field(default_factory=list)


@dataclass
class ApplicantInfo:
    applicant_id: uuid.UUID
    label: str  # "Applicant 1": what the model sees, never a name
    party_role: str
    is_primary: bool
    age_at_term_end: int | None


@dataclass
class AssessmentInputs:
    case_id: uuid.UUID
    purpose: str
    property_use: str | None
    case_status: str
    property_value_minor: int | None
    loan_amount_minor: int | None
    term_months: int | None
    applicants: list[ApplicantInfo]
    income: list[IncomeLine]
    commitments: list[CommitmentLine]
    expenditure_monthly_minor: int
    expenditure_items: int
    living_costs_monthly_minor: int
    living_costs_basis: str
    profile_fingerprint: str
    profile_approved: bool
    notes: list[str] = field(default_factory=list)

    @property
    def counted_income(self) -> list[IncomeLine]:
        return [line for line in self.income if line.counted]

    @property
    def gross_monthly_minor(self) -> int:
        return sum(line.qualifying_gross_monthly_minor for line in self.counted_income)

    @property
    def net_monthly_minor(self) -> int:
        return sum(line.qualifying_net_monthly_minor or 0 for line in self.counted_income)

    @property
    def commitments_monthly_minor(self) -> int:
        return sum(c.monthly_minor or 0 for c in self.commitments if c.counted)

    @property
    def excluded_income(self) -> list[IncomeLine]:
        return [line for line in self.income if not line.counted and line.gross_monthly_minor]

    def label(self, applicant_id: uuid.UUID | None) -> str:
        for a in self.applicants:
            if a.applicant_id == applicant_id:
                return a.label
        return "An applicant"


def _months_between(start: date, end: date) -> int:
    return (end.year - start.year) * 12 + (end.month - start.month) - (end.day < start.day)


def _period_key(f: FigureOut) -> str | None:
    end = f.period_end or f.period_start
    return f"{end:%Y-%m}" if end else None


def derive(
    snapshot: ProfileSnapshot,
    *,
    fingerprint: str,
    approved: bool,
    property_use: str | None,
    property_value_minor: int | None,
    loan_amount_minor: int | None,
    term_months: int | None,
    dates_of_birth: dict[uuid.UUID, date | None],
    policy: Policy,
    today: date,
) -> AssessmentInputs:
    term = term_months or policy.default_term_months
    active = [a for a in snapshot.applicants if a.status == "active"]
    active.sort(key=lambda a: (not a.is_primary, a.display_order))
    applicants: list[ApplicantInfo] = []
    for number, a in enumerate(active, start=1):
        dob = dates_of_birth.get(a.id)
        age_end = None
        if dob is not None:
            age_end = _months_between(dob, today) // 12 + (term + 11) // 12
        applicants.append(
            ApplicantInfo(a.id, f"Applicant {number}", a.party_role, a.is_primary, age_end)
        )
    labels = {a.applicant_id: a.label for a in applicants}

    by_item: dict[uuid.UUID, list[FigureOut]] = defaultdict(list)
    for f in snapshot.figures:
        by_item[f.item_id].append(f)
    corroborated: dict[uuid.UUID, list[str]] = defaultdict(list)
    for item in snapshot.support:
        for corroboration in item.corroborating:
            if corroboration.credit_date:
                corroborated[item.item_id].append(f"{corroboration.credit_date:%Y-%m}")

    income: list[IncomeLine] = []
    for src in snapshot.income_sources:
        if src.applicant_id not in labels:
            continue  # a removed applicant's income isn't part of the assessment
        figures = by_item.get(src.id, [])
        documents = [f for f in figures if f.source_kind == "document"]
        stated = [f for f in figures if f.source_kind != "document"]
        notes: list[str] = []
        kind = str(src.kind)

        def monthly_of(fs: list[FigureOut], measure: str, notes: list[str] = notes) -> list[int]:
            out = []
            for f in fs:
                amount = _money(f)
                if f.measure == measure and amount is not None:
                    m = _monthly(amount, f.frequency)
                    if m is None:
                        notes.append(f"A one-off {measure.replace('_', ' ')} is not counted.")
                    else:
                        out.append(m)
            return out

        def recent(fs: list[FigureOut]) -> tuple[list[FigureOut], list[str]]:
            periods = sorted({p for f in fs if (p := _period_key(f))}, reverse=True)
            keep = set(periods[: policy.payslip_months_required])
            chosen = [f for f in fs if _period_key(f) in keep or _period_key(f) is None]
            return chosen, sorted(keep)

        gross = net = None
        variable = 0
        periods: list[str] = []
        used: list[FigureOut] = []
        if kind in PAYROLL and documents:
            used, periods = recent(documents)
            grosses = monthly_of(used, "gross_pay")
            nets = monthly_of(used, "net_pay")
            gross = _avg(grosses) if grosses else None
            net = _avg(nets) if nets else None
            variable = sum(_avg(monthly_of(used, m)) for m in VARIABLE)
            if gross is None:
                basic = monthly_of(used, "basic_pay")
                if basic:
                    gross = _avg(basic) + variable
                    notes.append("Total gross pay isn't evidenced; basic and variable pay used.")
        elif kind == "property_rental":
            pool = documents or stated
            rents = monthly_of(pool, "gross_rent")
            costs = monthly_of(pool, "rental_costs")
            if rents:
                gross = _avg(rents) - (_avg(costs) if costs else 0)
                net = gross
                notes.append("Rental income counted after letting costs; tax not deducted.")
            used = pool
        elif kind in SINGLE_AMOUNT:
            pool = documents or stated
            amounts = monthly_of(pool, SINGLE_AMOUNT[kind]) or monthly_of(
                pool, "total_gross_income"
            )
            gross = _avg(amounts) if amounts else None
            used = pool
        elif kind in {"self_employment", "directorship", "other"}:
            pool = documents or stated
            profit = monthly_of(pool, "net_profit") or monthly_of(pool, "share_of_net_profit")
            totals = profit or monthly_of(pool, "total_gross_income")
            gross = _avg(totals) if totals else None
            used = pool
        if kind in PAYROLL and not documents:
            basic = monthly_of(stated, "basic_pay")
            total = monthly_of(stated, "total_gross_income")
            declared_amounts = basic or total
            gross = _avg(declared_amounts) if declared_amounts else None
            used = stated

        declared_values = monthly_of(stated, "basic_pay") or monthly_of(
            stated, "total_gross_income"
        )
        declared_gross = _avg(declared_values) if declared_values else None

        if gross is None:
            verification = "missing"
        elif any(f.source_kind == "document" for f in used):
            corroborating = set(corroborated.get(src.id, []))
            verification = "verified" if corroborating & set(periods) else "extracted"
        else:
            verification = "declared"

        inclusion = policy.variable_pay_inclusion_percent / Decimal(100)
        discount = int((Decimal(variable) * (1 - inclusion)).quantize(Decimal(1), ROUND_HALF_UP))
        if kind == "property_rental" and gross is not None:
            rental = policy.rental_inclusion_percent / Decimal(100)
            q_gross = int((Decimal(gross) * rental).quantize(Decimal(1), ROUND_HALF_UP))
            q_net: int | None = q_gross
        else:
            q_gross = max(0, (gross or 0) - discount)
            q_net = None
            if net is not None:
                if gross and discount:
                    q_net = int(
                        (Decimal(net) * Decimal(q_gross) / Decimal(gross)).quantize(
                            Decimal(1), ROUND_HALF_UP
                        )
                    )
                    notes.append(
                        "Net pay reduced in proportion to the variable pay not counted (inferred)."
                    )
                else:
                    q_net = net
        if discount:
            notes.append(
                f"{policy.variable_pay_inclusion_percent} % of variable pay (overtime, bonus, "
                "commission) counted."
            )
        counted = gross is not None and q_gross > 0
        if verification == "declared" and not policy.declared_only_income_counts:
            counted = False
            notes.append("Declared only: this policy counts evidenced income only.")
        if counted and q_net is None:
            notes.append("Net income isn't evidenced: excluded from net income (conservative).")

        attrs = {
            f.measure: f
            for f in figures
            if f.measure in {"probation_end_date", "contract_end_date"}
        }

        def date_attr(code: str, attrs: dict[str, FigureOut] = attrs) -> date | None:
            f = attrs.get(code)
            return getattr(f.value, "date", None) if f else None

        income.append(
            IncomeLine(
                income_source_id=src.id,
                applicant_id=src.applicant_id,
                applicant_label=labels[src.applicant_id],
                kind=kind,
                name=src.name,
                basis=str(src.basis) if src.basis else None,
                verification=verification,
                recurring=gross is not None,
                periods_evidenced=periods,
                gross_monthly_minor=gross,
                net_monthly_minor=net,
                variable_monthly_minor=variable,
                qualifying_gross_monthly_minor=q_gross if counted else 0,
                qualifying_net_monthly_minor=q_net if counted else None,
                counted=counted,
                declared_gross_monthly_minor=declared_gross,
                start_date=src.start_date,
                months_in_employment=(
                    _months_between(src.start_date, today) if src.start_date else None
                ),
                probation_end=date_attr("probation_end_date"),
                contract_end=date_attr("contract_end_date"),
                corroborated_periods=sorted(set(corroborated.get(src.id, []))),
                figure_ids=[f.id for f in used],
                notes=notes,
            )
        )

    commitments: list[CommitmentLine] = []
    for c in snapshot.commitments:
        figures = by_item.get(c.id, [])
        payments = [f for f in figures if f.measure == "monthly_payment" and _money(f) is not None]
        balance_f = [f for f in figures if f.measure == "outstanding_balance"]
        balance = _money(balance_f[-1]) if balance_f else None
        c_notes: list[str] = []
        monthly: int | None = None
        basis = "missing"
        if payments:
            latest = payments[-1]
            monthly = _monthly(_money(latest) or 0, latest.frequency)
            basis = "document" if latest.source_kind == "document" else "declared"
        elif str(c.kind) in REVOLVING and balance:
            monthly = int(
                (Decimal(balance) * policy.revolving_payment_percent / Decimal(100)).quantize(
                    Decimal(1), ROUND_HALF_UP
                )
            )
            basis = "assumed"
            c_notes.append(
                f"No payment stated: {policy.revolving_payment_percent} % of the balance assumed."
            )
        else:
            c_notes.append("No monthly payment recorded.")
        counted = str(c.status) == "open" and not c.to_be_repaid_before_completion
        if not counted:
            c_notes.append(
                "Settled or to be repaid before completion: not counted."
                if str(c.status) != "open" or c.to_be_repaid_before_completion
                else ""
            )
        commitments.append(
            CommitmentLine(
                commitment_id=c.id,
                kind=str(c.kind),
                creditor=c.creditor,
                status=str(c.status),
                monthly_minor=monthly,
                basis=basis,
                counted=counted and monthly is not None,
                outstanding_balance_minor=balance,
                notes=[x for x in c_notes if x],
            )
        )

    expenditure_total = 0
    expenditure_items = 0
    for e in snapshot.expenditure:
        for f in by_item.get(e.id, []):
            amount = _money(f)
            if f.measure == "amount" and amount is not None:
                m = _monthly(amount, f.frequency)
                if m is not None:
                    expenditure_total += m
                    expenditure_items += 1
    adults = max(1, sum(1 for a in applicants if a.party_role == "borrower") or len(applicants))
    if expenditure_items:
        living, basis = expenditure_total, "declared"
    else:
        living = (
            policy.default_living_costs_minor
            + (adults - 1) * policy.default_living_costs_additional_adult_minor
        )
        basis = "policy_default"

    return AssessmentInputs(
        case_id=snapshot.case.id,
        purpose=snapshot.case.purpose,
        property_use=property_use,
        case_status=snapshot.case.status,
        property_value_minor=property_value_minor,
        loan_amount_minor=loan_amount_minor,
        term_months=term,
        applicants=applicants,
        income=income,
        commitments=commitments,
        expenditure_monthly_minor=expenditure_total,
        expenditure_items=expenditure_items,
        living_costs_monthly_minor=living,
        living_costs_basis=basis,
        profile_fingerprint=fingerprint,
        profile_approved=approved,
        notes=[] if term_months else [f"No term on the case: {term} months assumed."],
    )


async def load(
    ctx: OperationContext, case_id: uuid.UUID, policy: Policy, today: date
) -> tuple[AssessmentInputs, ProfileSnapshot]:
    """Build the inputs from the case's current profile (read-only)."""
    built = await profile_service.snapshot(ctx, case_id)
    approval = await profile_service.approval_status(ctx, built.case)
    dobs = {
        a.id: a.date_of_birth
        for a in await ctx.session.scalars(select(Applicant).where(Applicant.case_id == case_id))
    }
    case = built.case
    inputs = derive(
        built.snapshot,
        fingerprint=built.fingerprint,
        approved=approval.status == "approved",
        property_use=case.property_use,
        property_value_minor=case.property_value_minor,
        loan_amount_minor=case.loan_amount_minor,
        term_months=case.term_months,
        dates_of_birth=dobs,
        policy=policy,
        today=today,
    )
    return inputs, built.snapshot
