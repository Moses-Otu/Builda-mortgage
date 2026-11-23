"""Evidence gaps: what's required, what's supplied, what's verified, and what's outstanding.

Requirements come from the policy and the applicant's circumstances, never a fixed checklist:
an employed applicant needs recent payslips and statements that show the salary arriving; a
landlord needs evidence of rent; a purchase needs deposit evidence; a declared figure needs a
document. Every gap says **what**, **for whom**, **which months** where relevant, and **why**.

Gap codes are stable (``payslips:<income source>``), so a gap that is still open on the next
run is the same gap, and an evidence request whose gap has closed is fulfilled.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from meip.assessment.inputs import PAYROLL, AssessmentInputs
from meip.assessment.policy import Policy
from meip.reconciliation.schemas import ChecksOut

DOCUMENT_FOR = {
    "employment": ("payslip", True),
    "directorship": ("payslip", True),
    "self_employment": ("SA302 tax calculation or accounts", False),
    "property_rental": ("tenancy agreement and rental statement", False),
    "pension": ("pension statement", False),
    "benefit": ("benefit award letter", False),
    "maintenance_received": ("court order or maintenance agreement", False),
    "investment": ("investment income statement", False),
    "other": ("evidence of this income", False),
}


@dataclass
class Gap:
    code: str
    category: str  # document | information | action
    title: str
    reason: str
    applicant_id: str | None = None
    applicant_label: str | None = None
    document_type: str | None = None
    months: list[str] = field(default_factory=list)
    supported_on_platform: bool = True
    affects_outcome: bool = True


def expected_months(today: date, count: int) -> list[str]:
    """The last ``count`` complete calendar months before ``today``."""
    out: list[str] = []
    year, month = today.year, today.month
    for _ in range(count):
        month -= 1
        if month == 0:
            year, month = year - 1, 12
        out.append(f"{year:04d}-{month:02d}")
    return sorted(out)


def _month_of_title(title: str) -> str | None:
    """'August 2026 salary' -> '2026-08'."""
    from datetime import datetime

    try:
        return f"{datetime.strptime(' '.join(title.split()[:2]), '%B %Y'):%Y-%m}"  # noqa: DTZ007
    except ValueError:
        return None


def identify(policy: Policy, inputs: AssessmentInputs, checks: ChecksOut, today: date) -> list[Gap]:
    gaps: list[Gap] = []

    # Case information
    for name, value, code in (
        ("loan amount", inputs.loan_amount_minor, "case:loan_amount"),
        ("property value", inputs.property_value_minor, "case:property_value"),
    ):
        if not value:
            gaps.append(
                Gap(
                    code,
                    "information",
                    f"The {name}",
                    f"Affordability, loan to value and the outcome need the {name}.",
                )
            )
    if not inputs.applicants:
        gaps.append(
            Gap("case:applicants", "information", "An applicant", "No applicant on the case.")
        )

    # Income evidence
    salary_disc: dict[str, set[str]] = {}
    # Why a salary credit couldn't be looked for (e.g. no statement covers the days after a
    # month-end pay date): quoted in the request so it asks for exactly the right dates.
    not_run: dict[str, dict[str, str]] = {}
    for f in checks.findings:
        if f.rule_code != "SALARY-CREDIT" or not f.income_source_id:
            continue
        month = _month_of_title(f.title)
        if not month:
            continue
        if f.status == "discrepancy":
            salary_disc.setdefault(str(f.income_source_id), set()).add(month)
        elif f.status == "not_run":
            not_run.setdefault(str(f.income_source_id), {})[month] = f.message

    for line in inputs.income:
        sid = str(line.income_source_id)
        doc_name, supported = DOCUMENT_FOR.get(line.kind, ("evidence of this income", False))
        if line.kind in PAYROLL:
            wanted = expected_months(today, policy.payslip_months_required)
            have = set(line.periods_evidenced)
            missing = [m for m in wanted if m not in have]
            if missing:
                gaps.append(
                    Gap(
                        f"payslips:{sid}",
                        "document",
                        f"Payslips for {', '.join(_label(m) for m in missing)}",
                        f"The policy needs the latest {policy.payslip_months_required} months' "
                        f"payslips; {len(have)} reviewed"
                        + (f" ({', '.join(_label(m) for m in sorted(have))})." if have else "."),
                        str(line.applicant_id),
                        line.applicant_label,
                        "payslip",
                        missing,
                    )
                )
            uncorroborated = [
                m
                for m in sorted(have)
                if m not in line.corroborated_periods and m not in salary_disc.get(sid, set())
            ]
            if have and uncorroborated:
                why = [not_run.get(sid, {}).get(m) for m in uncorroborated]
                detail = " ".join(w for w in why if w)
                gaps.append(
                    Gap(
                        f"statements:{sid}",
                        "document",
                        "Bank statements showing the "
                        f"{', '.join(_label(m) for m in uncorroborated)} salary",
                        "To verify the salary was received, a confirmed statement must show "
                        "each payslip's net pay arriving. "
                        f"Not yet shown for {', '.join(_label(m) for m in uncorroborated)}."
                        + (f" {detail}" if detail else ""),
                        str(line.applicant_id),
                        line.applicant_label,
                        "bank_statement",
                        uncorroborated,
                    )
                )
        elif line.verification in {"declared", "missing"}:
            gaps.append(
                Gap(
                    f"income:{sid}",
                    "document",
                    f"Evidence of {line.kind.replace('_', ' ')} income ({doc_name})",
                    "This income is declared only. "
                    + (
                        ""
                        if supported
                        else "This document type isn't read by the platform yet: the broker "
                        "reviews it and records the figures."
                    ),
                    str(line.applicant_id),
                    line.applicant_label,
                    doc_name,
                    supported_on_platform=supported,
                )
            )
        if line.kind in PAYROLL and line.basis is None:
            gaps.append(
                Gap(
                    f"employment_basis:{sid}",
                    "information",
                    "Employment type",
                    "Permanent, fixed term, contract…: the policy's employment criteria need it.",
                    str(line.applicant_id),
                    line.applicant_label,
                )
            )
        if line.kind in PAYROLL and line.start_date is None:
            gaps.append(
                Gap(
                    f"employment_start:{sid}",
                    "information",
                    "Employment start date",
                    "Needed for the time-in-employment criterion.",
                    str(line.applicant_id),
                    line.applicant_label,
                )
            )

    if not inputs.income:
        for a in inputs.applicants:
            if a.party_role == "borrower":
                gaps.append(
                    Gap(
                        f"income_sources:{a.applicant_id}",
                        "information",
                        "Income sources",
                        "No income is recorded for this applicant.",
                        str(a.applicant_id),
                        a.label,
                    )
                )

    for c in inputs.commitments:
        if c.counted is False and c.monthly_minor is None and c.status == "open":
            gaps.append(
                Gap(
                    f"commitment_payment:{c.commitment_id}",
                    "information",
                    f"Monthly payment for the {c.kind.replace('_', ' ')}",
                    "Existing commitments reduce affordability; the payment isn't recorded.",
                )
            )

    if inputs.living_costs_basis == "policy_default":
        gaps.append(
            Gap(
                "expenditure",
                "information",
                "Monthly living costs",
                "None are declared, so the policy's default was assumed. Declared costs make "
                "the affordability figure specific to this household.",
                affects_outcome=False,
            )
        )

    for f in checks.findings:
        if f.status == "discrepancy" and f.acknowledgement is None:
            gaps.append(
                Gap(
                    f"discrepancy:{f.key}",
                    "action",
                    f"Review the discrepancy: {f.title}",
                    f.message,
                )
            )

    if inputs.purpose == "purchase" and inputs.property_value_minor and inputs.loan_amount_minor:
        gaps.append(
            Gap(
                "deposit",
                "document",
                "Proof of deposit",
                "Savings statements or a gifted-deposit letter showing the deposit of "
                f"£{(inputs.property_value_minor - inputs.loan_amount_minor) / 100:,.0f}. "
                "This document type isn't read by the platform yet.",
                document_type="deposit evidence",
                supported_on_platform=False,
                affects_outcome=False,
            )
        )
    if policy.require_credit_report:
        gaps.append(
            Gap(
                "credit_report",
                "document",
                "Credit report",
                "The policy needs credit history; credit reports aren't read by the platform yet.",
                document_type="credit report",
                supported_on_platform=False,
            )
        )
    return gaps


def _label(month: str) -> str:
    year, m = month.split("-")
    return date(int(year), int(m), 1).strftime("%b %Y")
