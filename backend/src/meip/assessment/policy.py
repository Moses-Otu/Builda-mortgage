"""Versioned lending policies: the assumptions every assessment figure is computed from.

A policy is code, not a prompt and not database data: a frozen set of parameters with a
version code (``uk-resi-standard/1``). Like the check rule sets, a code's meaning never
changes; a changed parameter needs a new code. Every assessment records the code it used and
returns the parameters with its results, so each figure is traceable to its assumptions.

These are **generic, indicative UK residential assumptions**, not any lender's criteria and
not regulatory thresholds. They exist so a broker can see, before approaching a lender, how
an applicant's evidenced position compares with typical criteria.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any

from meip.core.errors import NotFoundError


@dataclass(frozen=True)
class Policy:
    code: str
    name: str
    description: str
    # --- borrowing caps ---------------------------------------------------------------
    max_lti: Decimal
    """Maximum loan as a multiple of annual qualifying gross income."""
    max_ltv_percent: Decimal
    """Maximum loan as a percentage of the property value (residential)."""
    # --- affordability ----------------------------------------------------------------
    default_rate_percent: Decimal
    """Product rate assumed when the broker gives none."""
    stress_margin_percent: Decimal
    """Added to the product rate for the stressed repayment."""
    stress_floor_percent: Decimal
    """The stressed rate is never below this."""
    default_term_months: int
    max_dti_percent: Decimal
    """(Existing commitments + stressed mortgage payment) / gross monthly income."""
    min_monthly_buffer_minor: int
    """Net disposable income that must remain after the stressed payment."""
    default_living_costs_minor: int
    """Monthly living costs for the first adult when none are declared (an assumption)."""
    default_living_costs_additional_adult_minor: int
    # --- income -------------------------------------------------------------------------
    min_annual_income_minor: int
    variable_pay_inclusion_percent: Decimal
    """Share of evidenced overtime, bonus and commission counted."""
    rental_inclusion_percent: Decimal
    """Share of net rental income (rent less costs) counted."""
    declared_only_income_counts: bool
    """Whether declared-only income counts toward borrowing (always shown as unverified)."""
    revolving_payment_percent: Decimal
    """Monthly payment assumed for a credit card or overdraft with a balance but no stated
    payment (an assumption, always labelled)."""
    eligible_bases: frozenset[str]
    min_months_in_employment: int
    max_age_at_term_end: int
    # --- evidence -------------------------------------------------------------------------
    payslip_months_required: int
    statement_months_required: int
    require_credit_report: bool
    """If true, credit history that hasn't been assessed blocks 'potentially eligible'."""
    supported_property_uses: frozenset[str] = field(
        default_factory=lambda: frozenset({"residential"})
    )

    def parameters(self) -> dict[str, Any]:
        """JSON-safe parameters, returned with every result."""
        out: dict[str, Any] = {}
        for key, value in asdict(self).items():
            if key in {"code", "name", "description"}:
                continue
            if isinstance(value, Decimal):
                out[key] = str(value)
            elif isinstance(value, frozenset):
                out[key] = sorted(value)
            else:
                out[key] = value
        return out


_COMMON: dict[str, Any] = {
    "default_rate_percent": Decimal("4.50"),
    "stress_margin_percent": Decimal("1.00"),
    "default_term_months": 300,
    "min_monthly_buffer_minor": 30_000,  # £300
    "default_living_costs_minor": 120_000,  # £1,200
    "default_living_costs_additional_adult_minor": 45_000,  # £450
    "min_annual_income_minor": 1_500_000,  # £15,000
    "rental_inclusion_percent": Decimal("75"),
    "revolving_payment_percent": Decimal("3"),
    "max_age_at_term_end": 75,
    "payslip_months_required": 3,
    "statement_months_required": 3,
    "require_credit_report": False,
}

STANDARD = Policy(
    code="uk-resi-standard/1",
    name="UK residential, standard (indicative)",
    description=(
        "Typical mainstream UK residential criteria: up to 4.5 times income, 95 % loan to "
        "value, payments stressed at the product rate plus 1 % (at least 7 %)."
    ),
    max_lti=Decimal("4.5"),
    max_ltv_percent=Decimal("95"),
    stress_floor_percent=Decimal("7.00"),
    max_dti_percent=Decimal("45"),
    variable_pay_inclusion_percent=Decimal("50"),
    declared_only_income_counts=True,
    eligible_bases=frozenset({"permanent", "fixed_term", "contract"}),
    min_months_in_employment=6,
    **_COMMON,
)

CAUTIOUS = Policy(
    code="uk-resi-cautious/1",
    name="UK residential, cautious (indicative)",
    description=(
        "Stricter criteria: up to 4 times income, 90 % loan to value, payments stressed at "
        "the product rate plus 1 % (at least 8 %), evidenced income only."
    ),
    max_lti=Decimal("4.0"),
    max_ltv_percent=Decimal("90"),
    stress_floor_percent=Decimal("8.00"),
    max_dti_percent=Decimal("40"),
    variable_pay_inclusion_percent=Decimal("0"),
    declared_only_income_counts=False,
    eligible_bases=frozenset({"permanent"}),
    min_months_in_employment=12,
    **_COMMON,
)

POLICIES: dict[str, Policy] = {p.code: p for p in (STANDARD, CAUTIOUS)}
DEFAULT_POLICY = STANDARD.code


def get_policy(code: str) -> Policy:
    policy = POLICIES.get(code)
    if policy is None:
        raise NotFoundError(f"Unknown policy '{code}'")
    return policy
