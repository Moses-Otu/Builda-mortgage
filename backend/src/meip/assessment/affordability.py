"""The affordability engine: pure, deterministic functions. No database, no model, no floats.

Money is integer pence; rates are ``Decimal`` percentages. Repayments are capital and interest
(annuity) payments, rounded half up to the penny; maximum loans are rounded down to the pound,
so an amount shown as affordable is never above the computed limit.

Definitions (each result carries them in ``assumptions``):

* **Qualifying income:** monthly gross and net income the policy counts (see ``inputs``).
* **Disposable income before the mortgage:** net income - existing commitments - living costs.
* **Stressed rate:** max(product rate + stress margin, stress floor).
* **Maximum affordable monthly repayment:** the lower of (disposable income - buffer) and
  (DTI cap x gross income - existing commitments), never below zero.
* **Maximum loan by affordability:** the loan whose stressed repayment equals that amount.
* **Maximum mortgage:** the lowest of the affordability, income-multiple (LTI) and
  loan-to-value (LTV) limits that can be computed. The lowest one is the binding constraint.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

from meip.assessment.policy import Policy

HUNDRED = Decimal(100)
TWELVE = Decimal(12)


def _pence(value: Decimal) -> int:
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _percent(numerator: int, denominator: int) -> Decimal | None:
    if denominator <= 0:
        return None
    return (Decimal(numerator) * HUNDRED / Decimal(denominator)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )


def monthly_repayment(loan_minor: int, annual_rate_percent: Decimal, term_months: int) -> int:
    """Capital and interest repayment per month, in pence."""
    if loan_minor <= 0 or term_months <= 0:
        return 0
    rate = annual_rate_percent / HUNDRED / TWELVE
    if rate == 0:
        return _pence(Decimal(loan_minor) / Decimal(term_months))
    factor = (1 + rate) ** term_months
    return _pence(Decimal(loan_minor) * rate * factor / (factor - 1))


def loan_for_repayment(payment_minor: int, annual_rate_percent: Decimal, term_months: int) -> int:
    """The largest loan (rounded down to the pound) whose repayment is ``payment_minor``."""
    if payment_minor <= 0 or term_months <= 0:
        return 0
    rate = annual_rate_percent / HUNDRED / TWELVE
    if rate == 0:
        principal = Decimal(payment_minor) * Decimal(term_months)
    else:
        principal = Decimal(payment_minor) * (1 - (1 + rate) ** -term_months) / rate
    pounds = (principal / HUNDRED).quantize(Decimal(1), rounding=ROUND_DOWN)
    return int(pounds) * 100


def stressed_rate(policy: Policy, rate_percent: Decimal) -> Decimal:
    return max(rate_percent + policy.stress_margin_percent, policy.stress_floor_percent)


@dataclass(frozen=True)
class AffordabilityInputs:
    gross_monthly_income_minor: int
    net_monthly_income_minor: int
    commitments_monthly_minor: int
    living_costs_monthly_minor: int
    living_costs_basis: str  # "declared" or "policy_default"
    property_value_minor: int | None
    loan_amount_minor: int | None
    rate_percent: Decimal
    term_months: int

    @property
    def deposit_minor(self) -> int | None:
        if self.property_value_minor is None or self.loan_amount_minor is None:
            return None
        return self.property_value_minor - self.loan_amount_minor


@dataclass(frozen=True)
class Limit:
    code: str  # "affordability" | "income_multiple" | "loan_to_value"
    max_loan_minor: int
    explanation: str


@dataclass(frozen=True)
class AffordabilityResult:
    policy_code: str
    inputs: AffordabilityInputs
    stressed_rate_percent: Decimal
    disposable_before_mortgage_minor: int
    max_affordable_monthly_repayment_minor: int
    limits: list[Limit]
    max_mortgage_minor: int | None
    binding_constraint: str | None
    # Requested loan (None when no loan amount was given)
    monthly_repayment_minor: int | None
    stressed_monthly_repayment_minor: int | None
    total_repayable_minor: int | None
    disposable_after_mortgage_minor: int | None
    dti_percent: Decimal | None
    ltv_percent: Decimal | None
    lti: Decimal | None
    deposit_minor: int | None
    min_deposit_minor: int | None
    requested_within_limits: bool | None
    shortfall_minor: int | None
    risks: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)


def assess(policy: Policy, i: AffordabilityInputs) -> AffordabilityResult:
    """Run the full calculation. Deterministic: the same inputs and policy give the same
    result."""
    stress = stressed_rate(policy, i.rate_percent)
    disposable = (
        i.net_monthly_income_minor - i.commitments_monthly_minor - i.living_costs_monthly_minor
    )
    by_buffer = disposable - policy.min_monthly_buffer_minor
    by_dti = (
        _pence(Decimal(i.gross_monthly_income_minor) * policy.max_dti_percent / HUNDRED)
        - i.commitments_monthly_minor
    )
    max_payment = max(0, min(by_buffer, by_dti))

    limits: list[Limit] = [
        Limit(
            "affordability",
            loan_for_repayment(max_payment, stress, i.term_months),
            f"Largest loan whose repayment at the stressed rate ({stress} %) over "
            f"{i.term_months} months fits the maximum affordable monthly repayment",
        )
    ]
    annual_gross = i.gross_monthly_income_minor * 12
    if annual_gross > 0:
        limits.append(
            Limit(
                "income_multiple",
                int(
                    (Decimal(annual_gross) * policy.max_lti / HUNDRED).quantize(
                        Decimal(1), rounding=ROUND_DOWN
                    )
                )
                * 100,
                f"{policy.max_lti} times annual qualifying gross income",
            )
        )
    if i.property_value_minor:
        limits.append(
            Limit(
                "loan_to_value",
                int(
                    (
                        Decimal(i.property_value_minor) * policy.max_ltv_percent / HUNDRED / HUNDRED
                    ).quantize(Decimal(1), rounding=ROUND_DOWN)
                )
                * 100,
                f"{policy.max_ltv_percent} % of the property value",
            )
        )
    binding = min(limits, key=lambda limit: limit.max_loan_minor)
    max_mortgage = binding.max_loan_minor

    risks: list[str] = []
    if disposable <= 0:
        risks.append("Net income does not cover existing commitments and living costs.")
    if i.living_costs_basis == "policy_default":
        risks.append(
            "No living costs are declared: the policy's default living costs were assumed."
        )

    payment = stressed = total = after = None
    dti = ltv = lti = None
    within: bool | None = None
    shortfall: int | None = None
    min_deposit: int | None = None
    if i.property_value_minor:
        min_deposit = i.property_value_minor - limits[-1].max_loan_minor
    if i.loan_amount_minor:
        payment = monthly_repayment(i.loan_amount_minor, i.rate_percent, i.term_months)
        stressed = monthly_repayment(i.loan_amount_minor, stress, i.term_months)
        total = payment * i.term_months
        after = disposable - stressed
        dti = _percent(i.commitments_monthly_minor + stressed, i.gross_monthly_income_minor)
        if i.property_value_minor:
            ltv = _percent(i.loan_amount_minor, i.property_value_minor)
        if annual_gross > 0:
            lti = (Decimal(i.loan_amount_minor) / Decimal(annual_gross)).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
        within = i.loan_amount_minor <= max_mortgage
        shortfall = max(0, i.loan_amount_minor - max_mortgage)
        if after < policy.min_monthly_buffer_minor:
            risks.append(
                "After the stressed repayment, disposable income is below the policy buffer."
            )
        if dti is not None and dti > policy.max_dti_percent:
            risks.append(f"Debt to income ({dti} %) exceeds the policy cap.")
        if ltv is not None and ltv > policy.max_ltv_percent:
            risks.append(f"Loan to value ({ltv} %) exceeds the policy cap.")
        if lti is not None and lti > policy.max_lti:
            risks.append(f"Loan to income ({lti}x) exceeds the policy cap.")

    assumptions = [
        f"Policy {policy.code}: {policy.name}.",
        f"Product rate {i.rate_percent} %; stressed at {stress} % "
        f"(rate + {policy.stress_margin_percent} %, at least {policy.stress_floor_percent} %).",
        f"Term {i.term_months} months, capital and interest.",
        f"Buffer £{policy.min_monthly_buffer_minor / 100:,.2f} a month; "
        f"DTI cap {policy.max_dti_percent} %.",
        "Living costs: "
        + ("as declared." if i.living_costs_basis == "declared" else "policy default."),
        "Indicative only: not a lending decision or advice.",
    ]
    return AffordabilityResult(
        policy_code=policy.code,
        inputs=i,
        stressed_rate_percent=stress,
        disposable_before_mortgage_minor=disposable,
        max_affordable_monthly_repayment_minor=max_payment,
        limits=limits,
        max_mortgage_minor=max_mortgage,
        binding_constraint=binding.code,
        monthly_repayment_minor=payment,
        stressed_monthly_repayment_minor=stressed,
        total_repayable_minor=total,
        disposable_after_mortgage_minor=after,
        dti_percent=dti,
        ltv_percent=ltv,
        lti=lti,
        deposit_minor=i.deposit_minor,
        min_deposit_minor=min_deposit,
        requested_within_limits=within,
        shortfall_minor=shortfall,
        risks=risks,
        assumptions=assumptions,
    )


# --- scenarios ------------------------------------------------------------------------


@dataclass(frozen=True)
class Scenario:
    """A hypothetical variation of the base case. Never replaces the base assessment."""

    label: str
    loan_amount_minor: int | None = None
    deposit_minor: int | None = None
    term_months: int | None = None
    rate_percent: Decimal | None = None
    extra_gross_monthly_minor: int = 0
    extra_net_monthly_minor: int = 0


def apply(base: AffordabilityInputs, s: Scenario) -> AffordabilityInputs:
    loan = base.loan_amount_minor
    if s.deposit_minor is not None and base.property_value_minor is not None:
        loan = max(0, base.property_value_minor - s.deposit_minor)
    if s.loan_amount_minor is not None:
        loan = s.loan_amount_minor
    return replace(
        base,
        loan_amount_minor=loan,
        term_months=s.term_months or base.term_months,
        rate_percent=s.rate_percent if s.rate_percent is not None else base.rate_percent,
        gross_monthly_income_minor=base.gross_monthly_income_minor + s.extra_gross_monthly_minor,
        net_monthly_income_minor=base.net_monthly_income_minor + s.extra_net_monthly_minor,
    )


def changed_assumptions(base: AffordabilityInputs, varied: AffordabilityInputs) -> list[str]:
    out: list[str] = []
    if varied.loan_amount_minor != base.loan_amount_minor:
        out.append("loan amount")
    if varied.term_months != base.term_months:
        out.append("term")
    if varied.rate_percent != base.rate_percent:
        out.append("rate")
    if varied.gross_monthly_income_minor != base.gross_monthly_income_minor:
        out.append("income")
    return out


def default_scenarios(policy: Policy, base: AffordabilityResult) -> list[Scenario]:
    """Alternatives worth showing when the requested loan exceeds the limits: what would
    have to change. Each changes one assumption; none changes evidenced facts."""
    i = base.inputs
    out: list[Scenario] = []
    if base.max_mortgage_minor is not None and i.loan_amount_minor:
        out.append(
            Scenario("Borrow the maximum indicated", loan_amount_minor=base.max_mortgage_minor)
        )
        if i.property_value_minor:
            needed = i.property_value_minor - base.max_mortgage_minor
            if i.deposit_minor is None or needed > i.deposit_minor:
                out.append(Scenario("Increase the deposit to fit", deposit_minor=needed))
    longest = 420
    if i.term_months < longest:
        out.append(Scenario(f"Extend the term to {longest // 12} years", term_months=longest))
    out.append(Scenario("Rate 1 % lower", rate_percent=max(Decimal(0), i.rate_percent - 1)))
    return out


def run_scenarios(
    policy: Policy, base: AffordabilityInputs, scenarios: list[Scenario]
) -> list[tuple[Scenario, AffordabilityResult, list[str]]]:
    out = []
    for s in scenarios:
        varied = apply(base, s)
        out.append((s, assess(policy, varied), changed_assumptions(base, varied)))
    return out
