"""Indicative pre-qualification: criteria evaluated against a policy. Pure and deterministic.

Each criterion is **met**, **not met**, **unknown** (the information or evidence to decide is
missing) or **not assessed** (the platform can't assess it yet, e.g. credit history; stated as
a limitation). The outcome follows from the criteria only:

1. **Insufficient information:** no counted income, or no loan amount or property value.
2. **Not currently eligible:** any criterion not met.
3. **Eligible subject to further verification:** none failed, but some unknown (or not
   assessed where the policy requires it).
4. **Potentially eligible:** every criterion met.

The outcome is **indicative**: it is not a lending decision or advice, and the broker decides.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from meip.assessment.affordability import AffordabilityResult
from meip.assessment.inputs import PAYROLL, AssessmentInputs
from meip.assessment.policy import Policy

OUTCOMES = (
    "potentially_eligible",
    "eligible_subject_to_verification",
    "not_currently_eligible",
    "insufficient_information",
)
OUTCOME_LABELS = {
    "potentially_eligible": "Potentially eligible",
    "eligible_subject_to_verification": "Eligible subject to further verification",
    "not_currently_eligible": "Not currently eligible",
    "insufficient_information": "Insufficient information",
}


@dataclass
class Criterion:
    code: str
    title: str
    status: str  # met | not_met | unknown | not_assessed
    detail: str
    applicant_label: str | None = None
    evidence: list[str] = field(default_factory=list)  # figure ids, finding keys


@dataclass
class Eligibility:
    policy_code: str
    outcome: str
    outcome_label: str
    criteria: list[Criterion]
    reasons: list[str]
    unmet: list[str]
    outstanding: list[str]
    indicative_range_minor: tuple[int, int] | None


def _gbp(minor: int) -> str:
    return f"£{minor / 100:,.0f}"


def evaluate(
    policy: Policy,
    inputs: AssessmentInputs,
    result: AffordabilityResult | None,
    *,
    open_discrepancies: list[str],
    acknowledged_discrepancies: list[str],
    today: date,
    verified_max_minor: int | None = None,
    outstanding_evidence: list[str] | None = None,
) -> Eligibility:
    """``verified_max_minor``: the maximum mortgage counting verified income only (the low end
    of the indicative range). ``outstanding_evidence``: titles of open evidence gaps that
    affect the outcome; any makes the outcome subject to verification at best."""
    c: list[Criterion] = []
    if outstanding_evidence:
        c.append(
            Criterion(
                "evidence_complete",
                "Evidence complete",
                "unknown",
                "Outstanding: " + "; ".join(outstanding_evidence[:6]) + ".",
            )
        )
    elif outstanding_evidence is not None:
        c.append(
            Criterion(
                "evidence_complete",
                "Evidence complete",
                "met",
                "The evidence the policy needs is on file.",
            )
        )
    counted = inputs.counted_income
    annual = inputs.gross_monthly_minor * 12

    # Income
    if not counted:
        c.append(
            Criterion("income_minimum", "Minimum income", "unknown", "No income is counted yet.")
        )
    elif annual >= policy.min_annual_income_minor:
        c.append(
            Criterion(
                "income_minimum",
                "Minimum income",
                "met",
                f"Qualifying income {_gbp(annual)} a year (minimum "
                f"{_gbp(policy.min_annual_income_minor)}).",
            )
        )
    else:
        c.append(
            Criterion(
                "income_minimum",
                "Minimum income",
                "not_met",
                f"Qualifying income {_gbp(annual)} a year is below the minimum "
                f"{_gbp(policy.min_annual_income_minor)}.",
            )
        )

    for line in inputs.income:
        if line.verification in {"declared", "missing"} and line.gross_monthly_minor is not None:
            c.append(
                Criterion(
                    "income_evidence",
                    "Income evidenced",
                    "unknown",
                    f"{line.kind.replace('_', ' ').capitalize()} income is declared only: "
                    "no reviewed document supports it.",
                    line.applicant_label,
                    [str(i) for i in line.figure_ids],
                )
            )
        if line.kind in PAYROLL and (line.counted or line.verification == "declared"):
            if line.basis is None:
                c.append(
                    Criterion(
                        "employment_basis",
                        "Employment type",
                        "unknown",
                        "The employment basis (permanent, fixed term, …) isn't recorded.",
                        line.applicant_label,
                    )
                )
            elif line.basis in policy.eligible_bases:
                c.append(
                    Criterion(
                        "employment_basis",
                        "Employment type",
                        "met",
                        f"{line.basis.replace('_', ' ').capitalize()} employment is accepted.",
                        line.applicant_label,
                    )
                )
            else:
                c.append(
                    Criterion(
                        "employment_basis",
                        "Employment type",
                        "not_met",
                        f"{line.basis.replace('_', ' ').capitalize()} employment isn't accepted "
                        "under this policy.",
                        line.applicant_label,
                    )
                )
            months = line.months_in_employment
            if months is None:
                c.append(
                    Criterion(
                        "employment_tenure",
                        "Time in employment",
                        "unknown",
                        "The employment start date isn't recorded.",
                        line.applicant_label,
                    )
                )
            else:
                c.append(
                    Criterion(
                        "employment_tenure",
                        "Time in employment",
                        "met" if months >= policy.min_months_in_employment else "not_met",
                        f"{months} months in this employment "
                        f"(minimum {policy.min_months_in_employment}).",
                        line.applicant_label,
                    )
                )
            if line.probation_end and line.probation_end > today:
                c.append(
                    Criterion(
                        "probation",
                        "Probation",
                        "unknown",
                        f"On probation until {line.probation_end:%d %b %Y}: lenders may need "
                        "confirmation it will be passed.",
                        line.applicant_label,
                    )
                )

    # Discrepancies
    if open_discrepancies:
        c.append(
            Criterion(
                "discrepancies",
                "Evidence consistent",
                "unknown",
                f"{len(open_discrepancies)} "
                + ("discrepancy" if len(open_discrepancies) == 1 else "discrepancies")
                + " not yet reviewed by the broker.",
                evidence=open_discrepancies,
            )
        )
    elif acknowledged_discrepancies:
        c.append(
            Criterion(
                "discrepancies",
                "Evidence consistent",
                "met",
                "Every discrepancy has been reviewed and acknowledged by the broker.",
                evidence=acknowledged_discrepancies,
            )
        )
    else:
        c.append(
            Criterion("discrepancies", "Evidence consistent", "met", "No discrepancies found.")
        )

    # Property and loan
    use = inputs.property_use
    if use is None or use in policy.supported_property_uses:
        c.append(
            Criterion(
                "property_use",
                "Property use",
                "met" if use else "unknown",
                "Residential." if use else "The property use isn't recorded.",
            )
        )
    else:
        c.append(
            Criterion(
                "property_use",
                "Property use",
                "not_met",
                f"{use.replace('_', ' ').capitalize()} isn't covered by this policy: "
                "refer to a specialist assessment.",
            )
        )

    if result is None or inputs.loan_amount_minor is None or not inputs.property_value_minor:
        missing = [
            name
            for name, value in (
                ("loan amount", inputs.loan_amount_minor),
                ("property value", inputs.property_value_minor),
            )
            if not value
        ]
        c.append(
            Criterion(
                "loan_details",
                "Loan details",
                "unknown",
                f"No {' or '.join(missing) or 'loan details'} on the case.",
            )
        )
    else:
        r = result
        c.append(
            Criterion(
                "loan_to_value",
                "Loan to value",
                "met" if (r.ltv_percent or 0) <= policy.max_ltv_percent else "not_met",
                f"{r.ltv_percent} % (maximum {policy.max_ltv_percent} %); deposit "
                f"{_gbp(r.deposit_minor or 0)}, at least {_gbp(r.min_deposit_minor or 0)} needed.",
            )
        )
        if counted:
            c.append(
                Criterion(
                    "income_multiple",
                    "Loan to income",
                    "met" if (r.lti or 0) <= policy.max_lti else "not_met",
                    f"{r.lti}x qualifying income (maximum {policy.max_lti}x).",
                )
            )
            after = r.disposable_after_mortgage_minor or 0
            limit = next(lim for lim in r.limits if lim.code == "affordability")
            fits = (inputs.loan_amount_minor or 0) <= limit.max_loan_minor
            c.append(
                Criterion(
                    "affordability",
                    "Affordability",
                    "met" if fits else "not_met",
                    (
                        f"Stressed repayment {_gbp(r.stressed_monthly_repayment_minor or 0)} "
                        f"a month "
                        f"leaves {_gbp(after)} after commitments and living costs; the "
                        f"affordability limit is {_gbp(limit.max_loan_minor)}."
                    ),
                )
            )
            c.append(
                Criterion(
                    "debt_to_income",
                    "Debt to income",
                    "met" if (r.dti_percent or 0) <= policy.max_dti_percent else "not_met",
                    f"{r.dti_percent} % of gross income (maximum {policy.max_dti_percent} %).",
                )
            )

    for a in inputs.applicants:
        if a.party_role != "borrower":
            continue
        if a.age_at_term_end is None:
            c.append(
                Criterion(
                    "age_at_term_end",
                    "Age at end of term",
                    "unknown",
                    "No date of birth recorded.",
                    a.label,
                )
            )
        else:
            c.append(
                Criterion(
                    "age_at_term_end",
                    "Age at end of term",
                    "met" if a.age_at_term_end <= policy.max_age_at_term_end else "not_met",
                    f"About {a.age_at_term_end} at the end of the term "
                    f"(maximum {policy.max_age_at_term_end}).",
                    a.label,
                )
            )

    c.append(
        Criterion(
            "credit_history",
            "Credit history",
            "unknown" if policy.require_credit_report else "not_assessed",
            "Not assessed: no credit report is held on the platform.",
        )
    )
    c.append(
        Criterion(
            "deposit_evidence",
            "Deposit evidenced",
            "not_assessed",
            "Not assessed: the source of the deposit isn't evidenced on the platform yet.",
        )
    )

    unmet = [f"{x.title}: {x.detail}" for x in c if x.status == "not_met"]
    outstanding = [f"{x.title}: {x.detail}" for x in c if x.status == "unknown"]
    if not counted or inputs.loan_amount_minor is None or not inputs.property_value_minor:
        outcome = "insufficient_information"
    elif unmet:
        outcome = "not_currently_eligible"
    elif outstanding:
        outcome = "eligible_subject_to_verification"
    else:
        outcome = "potentially_eligible"

    reasons: list[str] = []
    if outcome == "insufficient_information":
        reasons.append("Not enough information to assess: " + "; ".join(outstanding[:3]))
    elif outcome == "not_currently_eligible":
        reasons.extend(unmet)
    elif outcome == "eligible_subject_to_verification":
        reasons.append("No criterion fails, but some can't be confirmed yet.")
        reasons.extend(outstanding)
    else:
        reasons.append("Every assessed criterion is met with the evidence on file.")
    limitations = [f"{x.title}: {x.detail}" for x in c if x.status == "not_assessed"]
    reasons.extend(limitations)

    indicative: tuple[int, int] | None = None
    if result is not None and result.max_mortgage_minor is not None and counted:
        # Low end: verified income only (the caller's figure); high end: all counted income.
        low = verified_max_minor if verified_max_minor is not None else result.max_mortgage_minor
        indicative = (min(low, result.max_mortgage_minor), result.max_mortgage_minor)

    return Eligibility(
        policy_code=policy.code,
        outcome=outcome,
        outcome_label=OUTCOME_LABELS[outcome],
        criteria=c,
        reasons=reasons,
        unmet=unmet,
        outstanding=outstanding,
        indicative_range_minor=indicative,
    )
