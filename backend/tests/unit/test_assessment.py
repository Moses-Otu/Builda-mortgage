"""The agentic assessment's deterministic parts and its planners. SYNTHETIC data only.

Affordability arithmetic, policy outcomes, evidence gaps and income derivation are pure
functions; the planners are tested for the actions they choose in different circumstances,
and the OpenAI planner against recorded Responses API bodies (no network)."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from meip.assessment import affordability as engine
from meip.assessment import eligibility as elig
from meip.assessment import evidence
from meip.assessment.inputs import (
    ApplicantInfo,
    AssessmentInputs,
    CommitmentLine,
    IncomeLine,
    derive,
)
from meip.assessment.planners import (
    OpenAIPlanner,
    PlannedCall,
    PlannerUnavailableError,
    RulesPlanner,
)
from meip.assessment.policy import CAUTIOUS, STANDARD, get_policy
from meip.assessment.tools import TOOLS, ToolError, idempotency_key, json_schema, parse_args
from meip.core.config import AuthMode, Environment, Settings
from meip.core.errors import NotFoundError
from meip.profile.schemas import ProfileSnapshot
from meip.reconciliation.schemas import ChecksOut

TODAY = date(2026, 11, 23)
A1 = uuid.uuid4()


def inputs(**kw: Any) -> AffordabilityInputsT:
    base: dict[str, Any] = {
        "gross_monthly_income_minor": 375_000,
        "net_monthly_income_minor": 280_584,
        "commitments_monthly_minor": 0,
        "living_costs_monthly_minor": 120_000,
        "living_costs_basis": "policy_default",
        "property_value_minor": 25_000_000,
        "loan_amount_minor": 15_000_000,
        "rate_percent": Decimal("4.50"),
        "term_months": 300,
    }
    base.update(kw)
    return engine.AffordabilityInputs(**base)


AffordabilityInputsT = engine.AffordabilityInputs


# --- affordability engine ------------------------------------------------------------------


def test_repayment_matches_the_standard_annuity_and_its_inverse() -> None:
    assert engine.monthly_repayment(20_000_000, Decimal("4.5"), 300) == 111_166  # £1,111.66
    assert engine.loan_for_repayment(111_167, Decimal("4.5"), 300) == 20_000_000
    assert engine.monthly_repayment(1_200_000, Decimal("0"), 12) == 100_000
    assert engine.monthly_repayment(0, Decimal("5"), 300) == 0


def test_stressed_rate_has_a_margin_and_a_floor() -> None:
    assert engine.stressed_rate(STANDARD, Decimal("4.5")) == Decimal("7.00")  # floor
    assert engine.stressed_rate(STANDARD, Decimal("6.5")) == Decimal("7.50")  # margin
    assert engine.stressed_rate(CAUTIOUS, Decimal("4.5")) == Decimal("8.00")


def test_a_comfortable_loan_is_within_every_limit() -> None:
    r = engine.assess(STANDARD, inputs())
    assert r.requested_within_limits is True
    assert r.shortfall_minor == 0
    assert r.ltv_percent == Decimal("60.00")
    assert r.lti == Decimal("3.33")
    assert r.monthly_repayment_minor == engine.monthly_repayment(15_000_000, Decimal("4.5"), 300)
    assert r.total_repayable_minor == r.monthly_repayment_minor * 300
    assert {limit.code for limit in r.limits} == {
        "affordability",
        "income_multiple",
        "loan_to_value",
    }
    assert r.max_mortgage_minor == min(limit.max_loan_minor for limit in r.limits)


def test_the_binding_constraint_is_the_lowest_limit() -> None:
    r = engine.assess(STANDARD, inputs(loan_amount_minor=24_000_000))
    assert r.requested_within_limits is False
    assert r.binding_constraint == "affordability"
    assert r.max_mortgage_minor is not None
    assert r.shortfall_minor == 24_000_000 - r.max_mortgage_minor
    assert any("buffer" in x for x in r.risks)
    small_deposit = engine.assess(
        STANDARD, inputs(property_value_minor=12_000_000, loan_amount_minor=11_900_000)
    )
    assert small_deposit.binding_constraint == "loan_to_value"
    assert small_deposit.ltv_percent == Decimal("99.17")


def test_commitments_reduce_what_can_be_borrowed() -> None:
    free = engine.assess(STANDARD, inputs())
    loaded = engine.assess(STANDARD, inputs(commitments_monthly_minor=50_000))
    assert (
        loaded.max_affordable_monthly_repayment_minor < free.max_affordable_monthly_repayment_minor
    )
    assert loaded.dti_percent is not None
    assert free.dti_percent is not None
    assert loaded.dti_percent > free.dti_percent


def test_no_disposable_income_affords_nothing() -> None:
    r = engine.assess(STANDARD, inputs(living_costs_monthly_minor=300_000))
    assert r.max_affordable_monthly_repayment_minor == 0
    assert r.binding_constraint == "affordability"
    assert r.max_mortgage_minor == 0
    assert any("does not cover" in x for x in r.risks)


def test_scenarios_change_only_their_assumptions_and_never_the_base() -> None:
    base_inputs = inputs(loan_amount_minor=24_000_000)
    base = engine.assess(STANDARD, base_inputs)
    results = engine.run_scenarios(STANDARD, base_inputs, engine.default_scenarios(STANDARD, base))
    labels = [s.label for s, _, _ in results]
    assert "Borrow the maximum indicated" in labels
    assert "Increase the deposit to fit" in labels
    for s, r, changed in results:
        if s.label == "Borrow the maximum indicated":
            assert r.requested_within_limits is True
            assert changed == ["loan amount"]
        if s.label.startswith("Extend the term"):
            assert changed == ["term"]
            assert r.max_mortgage_minor >= base.max_mortgage_minor  # type: ignore[operator]
    assert base_inputs.loan_amount_minor == 24_000_000  # unchanged
    assert engine.assess(STANDARD, base_inputs) == base  # deterministic


def test_policies_are_versioned_and_expose_every_parameter() -> None:
    assert get_policy("uk-resi-standard/1") is STANDARD
    with pytest.raises(NotFoundError):
        get_policy("uk-resi-standard/2")
    params = STANDARD.parameters()
    assert params["max_lti"] == "4.5"
    assert params["eligible_bases"] == [
        "contract",
        "fixed_term",
        "permanent",
    ]
    json.dumps(params)  # JSON-safe


# --- eligibility ---------------------------------------------------------------------------


def line(**kw: Any) -> IncomeLine:
    base: dict[str, Any] = {
        "income_source_id": uuid.uuid4(),
        "applicant_id": A1,
        "applicant_label": "Applicant 1",
        "kind": "employment",
        "name": None,
        "basis": "permanent",
        "verification": "verified",
        "recurring": True,
        "periods_evidenced": ["2026-08", "2026-09", "2026-10"],
        "gross_monthly_minor": 375_000,
        "net_monthly_minor": 280_584,
        "variable_monthly_minor": 0,
        "qualifying_gross_monthly_minor": 375_000,
        "qualifying_net_monthly_minor": 280_584,
        "counted": True,
        "declared_gross_monthly_minor": None,
        "start_date": date(2020, 1, 1),
        "months_in_employment": 82,
        "probation_end": None,
        "contract_end": None,
        "corroborated_periods": ["2026-08", "2026-09", "2026-10"],
        "figure_ids": [],
    }
    base.update(kw)
    return IncomeLine(**base)


def case_inputs(**kw: Any) -> AssessmentInputs:
    base: dict[str, Any] = {
        "case_id": uuid.uuid4(),
        "purpose": "purchase",
        "property_use": "residential",
        "case_status": "draft",
        "property_value_minor": 25_000_000,
        "loan_amount_minor": 15_000_000,
        "term_months": 300,
        "applicants": [ApplicantInfo(A1, "Applicant 1", "borrower", True, 60)],
        "income": [line()],
        "commitments": [],
        "expenditure_monthly_minor": 0,
        "expenditure_items": 0,
        "living_costs_monthly_minor": 120_000,
        "living_costs_basis": "policy_default",
        "profile_fingerprint": "f" * 64,
        "profile_approved": False,
    }
    base.update(kw)
    return AssessmentInputs(**base)


def evaluate(ci: AssessmentInputs, policy: Any = STANDARD, **kw: Any) -> elig.Eligibility:
    from meip.assessment.tools import base_inputs

    result = engine.assess(policy, base_inputs(ci, policy)) if ci.counted_income else None
    return elig.evaluate(
        policy,
        ci,
        result,
        open_discrepancies=kw.pop("open_discrepancies", []),
        acknowledged_discrepancies=[],
        today=TODAY,
        outstanding_evidence=kw.pop("outstanding", []),
        **kw,
    )


def test_every_criterion_met_is_potentially_eligible() -> None:
    e = evaluate(case_inputs())
    assert e.outcome == "potentially_eligible", [c for c in e.criteria if c.status != "met"]
    # Limitations are stated, never assumed away.
    assert {c.code for c in e.criteria if c.status == "not_assessed"} == {
        "credit_history",
        "deposit_evidence",
    }
    assert any("Not assessed" in r for r in e.reasons)


def test_outstanding_evidence_or_discrepancies_make_it_subject_to_verification() -> None:
    assert (
        evaluate(case_inputs(), outstanding=["Bank statements for Oct 2026"]).outcome
        == "eligible_subject_to_verification"
    )
    e = evaluate(case_inputs(), open_discrepancies=["k1"])
    assert e.outcome == "eligible_subject_to_verification"
    assert any(c.code == "discrepancies" and c.status == "unknown" for c in e.criteria)


def test_a_failed_criterion_is_not_currently_eligible_with_its_reasons() -> None:
    e = evaluate(case_inputs(loan_amount_minor=24_000_000))
    assert e.outcome == "not_currently_eligible"
    assert {c.code for c in e.criteria if c.status == "not_met"} >= {
        "income_multiple",
        "affordability",
    }
    assert e.unmet
    assert all(":" in u for u in e.unmet)


def test_ineligible_employment_and_property_use_fail() -> None:
    e = evaluate(case_inputs(income=[line(basis="zero_hours")], property_use="buy_to_let"))
    failed = {c.code for c in e.criteria if c.status == "not_met"}
    assert {"employment_basis", "property_use"} <= failed


def test_missing_loan_details_or_income_is_insufficient_information() -> None:
    assert evaluate(case_inputs(loan_amount_minor=None)).outcome == "insufficient_information"
    nothing = case_inputs(
        income=[
            line(
                counted=False,
                qualifying_gross_monthly_minor=0,
                verification="missing",
                gross_monthly_minor=None,
            )
        ]
    )
    assert evaluate(nothing).outcome == "insufficient_information"


def test_declared_only_income_is_never_treated_as_verified() -> None:
    declared = case_inputs(
        income=[line(verification="declared", corroborated_periods=[], periods_evidenced=[])]
    )
    e = evaluate(declared)
    assert e.outcome == "eligible_subject_to_verification"
    assert any(c.code == "income_evidence" and c.status == "unknown" for c in e.criteria)


def test_the_indicative_range_runs_from_verified_income_to_all_counted_income() -> None:
    e = evaluate(case_inputs(), verified_max_minor=10_000_000)
    assert e.indicative_range_minor is not None
    low, high = e.indicative_range_minor
    assert low == 10_000_000
    assert high > low


# --- evidence gaps --------------------------------------------------------------------------


def no_checks() -> ChecksOut:
    return ChecksOut(ruleset_versions=[], parameters={}, findings=[])


def test_gaps_are_contextual_and_say_why() -> None:
    ci = case_inputs(
        income=[
            line(periods_evidenced=["2026-10"], corroborated_periods=[]),
            line(
                kind="property_rental",
                verification="declared",
                basis=None,
                periods_evidenced=[],
                corroborated_periods=[],
            ),
        ],
        living_costs_basis="policy_default",
    )
    gaps = {g.code.split(":")[0]: g for g in evidence.identify(STANDARD, ci, no_checks(), TODAY)}
    assert gaps["payslips"].months == ["2026-08", "2026-09"]
    assert gaps["statements"].months == ["2026-10"]
    assert "salary" in gaps["statements"].reason
    assert gaps["income"].supported_on_platform is False  # a tenancy agreement: not read yet
    assert gaps["deposit"].affects_outcome is False
    assert gaps["expenditure"].category == "information"


def test_a_fully_evidenced_employment_has_no_document_gaps() -> None:
    gaps = evidence.identify(
        STANDARD, case_inputs(living_costs_basis="declared"), no_checks(), TODAY
    )
    assert [g.code for g in gaps if g.category == "document" and g.affects_outcome] == []


def test_expected_months_are_the_last_complete_months() -> None:
    assert evidence.expected_months(date(2026, 1, 15), 3) == ["2025-10", "2025-11", "2025-12"]


# --- income derivation (no double counting, normalisation, verification) ---------------------


def _snapshot(
    figures: list[dict[str, Any]],
    *,
    corroborated: list[str] | None = None,
    kind: str = "employment",
) -> tuple[ProfileSnapshot, uuid.UUID]:
    src = uuid.uuid4()
    now = datetime(2026, 11, 1, tzinfo=UTC).isoformat()
    body: dict[str, Any] = {
        "rules": "profile/1",
        "case": {
            "id": str(uuid.uuid4()),
            "external_reference": None,
            "purpose": "purchase",
            "status": "draft",
        },
        "applicants": [
            {
                "id": str(A1),
                "display_order": 1,
                "party_role": "borrower",
                "is_primary": True,
                "status": "active",
                "first_name": "Alex",
                "middle_names": None,
                "last_name": "Example",
            }
        ],
        "income_sources": [
            {
                "id": str(src),
                "case_id": str(uuid.uuid4()),
                "applicant_id": str(A1),
                "kind": kind,
                "name": None,
                "basis": "permanent",
                "is_primary": True,
                "start_date": "2020-01-01",
                "end_date": None,
                "ownership_share": None,
                "created_at": now,
                "updated_at": now,
                "version": 1,
            }
        ],
        "commitments": [],
        "expenditure": [],
        "figures": [
            {
                "id": str(uuid.uuid4()),
                "item_kind": "income_source",
                "item_id": str(src),
                "measure": f["measure"],
                "qualifier": f.get("qualifier"),
                "value": {"type": "money", "amount_minor": f["amount"], "currency": "GBP"},
                "frequency": f.get("frequency", "monthly"),
                "period_start": f.get("start"),
                "period_end": f.get("end"),
                "tax_year": None,
                "as_of_date": None,
                "source_kind": f.get("source", "document"),
                "origin": "extracted" if f.get("source", "document") == "document" else "declared",
                "recorded_by": None,
                "recorded_at": now,
                "evidence": None,
                "history": [],
                "cited_by": [],
            }
            for f in figures
        ],
        "bank_accounts": [],
        "checks": {"ruleset_versions": [], "parameters": {}, "findings": []},
        "documents": [],
        "support": [
            {
                "item_kind": "income_source",
                "item_id": str(src),
                "declared": [],
                "documentary": [],
                "corroborating": [
                    {
                        "finding_key": "k",
                        "payslip_document_id": None,
                        "credit_date": f"{m}-28",
                        "amount_minor": 1,
                        "account_id": str(uuid.uuid4()),
                        "account_ending": "4821",
                        "account_label": None,
                        "joint": False,
                        "holder_ids": [],
                        "statement_document_id": str(uuid.uuid4()),
                        "row_number": 1,
                        "scope": "x",
                    }
                    for m in (corroborated or [])
                ],
                "findings": {"discrepancies": [], "not_run": [], "passed": []},
            }
        ],
        "members": [],
    }
    return ProfileSnapshot.model_validate(body), src


def _derive(snapshot: ProfileSnapshot, policy: Any = STANDARD) -> AssessmentInputs:
    return derive(
        snapshot,
        fingerprint="f" * 64,
        approved=False,
        property_use="residential",
        property_value_minor=25_000_000,
        loan_amount_minor=15_000_000,
        term_months=300,
        dates_of_birth={A1: date(1990, 1, 1)},
        policy=policy,
        today=TODAY,
    )


def payslip(
    month: str, gross: int, net: int, *, overtime: int = 0, basic: int | None = None
) -> list[dict[str, Any]]:
    end = f"{month}-28"
    rows = [
        {"measure": "gross_pay", "amount": gross, "end": end},
        {"measure": "net_pay", "amount": net, "end": end},
        {
            "measure": "basic_pay",
            "amount": basic if basic is not None else gross - overtime,
            "end": end,
        },
    ]
    if overtime:
        rows.append({"measure": "overtime", "amount": overtime, "end": end})
    return rows


def test_payroll_income_uses_gross_pay_once_and_discounts_variable_pay() -> None:
    figures = [
        *payslip("2026-08", 400_000, 300_000, overtime=50_000),
        *payslip("2026-09", 400_000, 300_000, overtime=50_000),
    ]
    figures.append(
        {"measure": "basic_pay", "amount": 6_000_000, "frequency": "annual", "source": "declared"}
    )
    ci = _derive(_snapshot(figures)[0])
    (x,) = ci.income
    assert x.gross_monthly_minor == 400_000  # gross pay, not gross + basic + overtime
    assert x.variable_monthly_minor == 50_000
    assert x.qualifying_gross_monthly_minor == 375_000  # 50 % of overtime counted
    assert x.qualifying_net_monthly_minor == 281_250  # net scaled in proportion (inferred)
    assert x.declared_gross_monthly_minor == 500_000  # kept for comparison, not counted
    assert x.verification == "extracted"  # documents, but no salary credit
    assert ci.gross_monthly_minor == 375_000


def test_a_salary_credit_in_an_evidenced_month_makes_it_verified() -> None:
    figures = payslip("2026-10", 375_000, 280_584)
    ci = _derive(_snapshot(figures, corroborated=["2026-10"])[0])
    assert ci.income[0].verification == "verified"


def test_declared_only_income_counts_only_where_the_policy_allows() -> None:
    figures = [
        {"measure": "basic_pay", "amount": 4_500_000, "frequency": "annual", "source": "declared"}
    ]
    snap = _snapshot(figures)[0]
    standard = _derive(snap).income[0]
    assert standard.verification == "declared"
    assert standard.counted
    assert standard.qualifying_gross_monthly_minor == 375_000
    assert standard.qualifying_net_monthly_minor is None  # net isn't evidenced: never invented
    cautious = _derive(snap, CAUTIOUS).income[0]
    assert not cautious.counted


def test_rental_income_is_counted_after_costs_at_the_policy_share_and_one_offs_never() -> None:
    figures = [
        {"measure": "gross_rent", "amount": 100_000, "source": "declared"},
        {"measure": "rental_costs", "amount": 20_000, "source": "declared"},
    ]
    x = _derive(_snapshot(figures, kind="property_rental")[0]).income[0]
    assert x.gross_monthly_minor == 80_000
    assert x.qualifying_gross_monthly_minor == 60_000  # 75 %
    weekly = [
        {"measure": "gross_rent", "amount": 30_000, "frequency": "weekly", "source": "declared"}
    ]
    assert (
        _derive(_snapshot(weekly, kind="property_rental")[0]).income[0].gross_monthly_minor
        == 130_000
    )
    one_off = [
        {"measure": "gross_rent", "amount": 500_000, "frequency": "one_off", "source": "declared"}
    ]
    y = _derive(_snapshot(one_off, kind="property_rental")[0]).income[0]
    assert y.gross_monthly_minor is None
    assert not y.counted


def test_commitments_are_counted_monthly_and_revolving_balances_are_labelled_assumptions() -> None:
    ci = case_inputs(
        commitments=[
            CommitmentLine(
                uuid.uuid4(), "personal_loan", None, "open", 25_000, "declared", True, None
            ),
            CommitmentLine(
                uuid.uuid4(), "car_finance", None, "open", 30_000, "declared", False, None
            ),
        ]
    )
    assert ci.commitments_monthly_minor == 25_000


# --- tools ---------------------------------------------------------------------------------


def test_every_tool_has_a_schema_with_a_why_and_validates_its_input() -> None:
    assert len(TOOLS) == 16
    for tool in TOOLS.values():
        schema = json_schema(tool)
        assert "why" in schema["properties"]
        assert schema["additionalProperties"] is False
    with pytest.raises(ToolError) as exc:
        parse_args(TOOLS["record_finding"], {"title": "x", "severity": "fatal"})
    assert exc.value.code == "invalid_input"
    with pytest.raises(ToolError):
        parse_args(TOOLS["get_assessment_context"], {"unexpected": 1})


def test_the_idempotency_key_ignores_key_order() -> None:
    assert idempotency_key("t", {"a": 1, "b": 2}) == idempotency_key("t", {"b": 2, "a": 1})
    assert idempotency_key("t", {"a": 1}) != idempotency_key("u", {"a": 1})


# --- the rules planner chooses actions from circumstances -------------------------------------


async def drive(
    planner: RulesPlanner, world: dict[str, dict[str, Any]], limit: int = 30
) -> list[PlannedCall]:
    calls: list[PlannedCall] = []
    for _ in range(limit):
        call = await planner.next()
        calls.append(call)
        planner.observe(call, world.get(call.tool, {"ok": True}), None)
        if call.tool == "generate_assessment_report":
            break
    return calls


def world(**over: Any) -> dict[str, dict[str, Any]]:
    w: dict[str, dict[str, Any]] = {
        "get_assessment_context": {
            "case": {"loan_amount_minor": 15_000_000, "property_value_minor": 25_000_000}
        },
        "reconcile_financial_evidence": {"discrepancies": []},
        "validate_income": {"qualifying_gross_monthly_minor": 375_000, "sources": []},
        "identify_evidence_gaps": {"gaps": []},
        "calculate_affordability": {"requested_within_limits": True},
        "evaluate_eligibility": {
            "outcome": "potentially_eligible",
            "outcome_label": "Potentially eligible",
        },
    }
    w.update(over)
    return w


async def test_a_clean_case_runs_straight_to_the_report() -> None:
    tools = [c.tool for c in await drive(RulesPlanner(), world())]
    assert tools[0] == "get_assessment_context"
    assert tools[-1] == "generate_assessment_report"
    for absent in (
        "record_finding",
        "escalate_to_broker",
        "simulate_mortgage_scenarios",
        "generate_evidence_request",
    ):
        assert absent not in tools
    assert "calculate_affordability" in tools
    assert "evaluate_eligibility" in tools


async def test_a_discrepancy_is_investigated_then_escalated() -> None:
    disc = {
        "key": "abc",
        "title": "September 2026 salary",
        "message": "£100 short",
        "analysis": ["Expected £2,805.84"],
        "acknowledged": False,
    }
    calls = await drive(
        RulesPlanner(),
        world(
            reconcile_financial_evidence={"discrepancies": [disc]},
            evaluate_eligibility={"outcome": "eligible_subject_to_verification"},
        ),
    )
    tools = [c.tool for c in calls]
    finding = next(c for c in calls if c.tool == "record_finding")
    assert finding.args["refs"] == ["abc"]
    assert tools.index("record_finding") < tools.index("escalate_to_broker")
    escalation = next(c for c in calls if c.tool == "escalate_to_broker")
    assert "abc" in escalation.args["refs"]


async def test_an_acknowledged_discrepancy_is_not_escalated() -> None:
    disc = {"key": "abc", "title": "t", "message": "m", "acknowledged": True}
    tools = [
        c.tool
        for c in await drive(
            RulesPlanner(), world(reconcile_financial_evidence={"discrepancies": [disc]})
        )
    ]
    assert "escalate_to_broker" not in tools
    assert "record_finding" not in tools


async def test_a_shortfall_explores_scenarios_and_a_failed_criterion_escalates() -> None:
    tools = [
        c.tool
        for c in await drive(
            RulesPlanner(),
            world(
                calculate_affordability={"requested_within_limits": False, "max_mortgage_minor": 1},
                evaluate_eligibility={
                    "outcome": "not_currently_eligible",
                    "unmet": ["Loan to income: 5.3x"],
                },
            ),
        )
    ]
    assert "simulate_mortgage_scenarios" in tools
    assert "escalate_to_broker" in tools


async def test_missing_loan_details_skip_affordability() -> None:
    tools = [
        c.tool for c in await drive(RulesPlanner(), world(get_assessment_context={"case": {}}))
    ]
    assert "calculate_affordability" not in tools
    assert "evaluate_eligibility" in tools


async def test_gaps_become_evidence_requests_for_documents_and_information_only() -> None:
    gaps = [
        {"code": "payslips:1", "category": "document", "title": "Payslips", "reason": "r"},
        {"code": "expenditure", "category": "information", "title": "Costs", "reason": "r"},
        {"code": "discrepancy:k", "category": "action", "title": "Review", "reason": "r"},
    ]
    calls = await drive(RulesPlanner(), world(identify_evidence_gaps={"gaps": gaps}))
    request = next(c for c in calls if c.tool == "generate_evidence_request")
    assert request.args["gap_codes"] == ["payslips:1", "expenditure"]
    status = next(c for c in calls if c.tool == "update_assessment_status")
    assert status.args["stage"] == "awaiting_evidence"


async def test_every_rules_call_names_a_registered_tool_with_valid_arguments() -> None:
    disc = {"key": "abc", "title": "t", "message": "m", "acknowledged": False}
    for call in await drive(
        RulesPlanner(),
        world(
            reconcile_financial_evidence={"discrepancies": [disc]},
            calculate_affordability={"requested_within_limits": False},
            evaluate_eligibility={"outcome": "not_currently_eligible", "unmet": ["x"]},
        ),
    ):
        parse_args(TOOLS[call.tool], call.args)  # raises if invalid
        assert call.rationale


# --- the OpenAI planner (recorded responses; no network) -------------------------------------


def _settings() -> Settings:
    return Settings(
        env=Environment.TEST,
        auth_mode=AuthMode.DEV,
        openai_api_key=SecretStr("sk-test"),
        agent_planner="openai",
    )


def _fc(name: str, args: dict[str, Any], call_id: str) -> dict[str, Any]:
    return {
        "type": "function_call",
        "name": name,
        "arguments": json.dumps(args),
        "call_id": call_id,
        "id": "fc_x",
    }


def _client(bodies: list[Any], seen: list[dict[str, Any]]) -> httpx.AsyncClient:
    queue = list(bodies)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        body = queue.pop(0)
        if isinstance(body, int):
            return httpx.Response(body, json={"error": "x"})
        return httpx.Response(200, json=body)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_the_model_chooses_tools_and_explains_why() -> None:
    seen: list[dict[str, Any]] = []
    client = _client(
        [
            {
                "status": "completed",
                "output": [_fc("get_assessment_context", {"why": "Start."}, "c1")],
            },
            {
                "status": "completed",
                "output": [
                    _fc(
                        "record_finding",
                        {"title": "t", "detail": "d", "severity": "info", "why": "Note it."},
                        "c2",
                    )
                ],
            },
        ],
        seen,
    )
    planner = OpenAIPlanner(_settings(), client=client, backoff=0)
    first = await planner.next()
    assert (first.tool, first.args, first.rationale) == ("get_assessment_context", {}, "Start.")
    planner.observe(first, {"case": {}}, None)
    second = await planner.next()
    assert second.tool == "record_finding"
    assert "why" not in second.args
    request = seen[1]
    assert request["store"] is False
    assert request["parallel_tool_calls"] is False
    assert {t["name"] for t in request["tools"]} == set(TOOLS)
    outputs = [i for i in request["input"] if i.get("type") == "function_call_output"]
    assert outputs == [{"type": "function_call_output", "call_id": "c1", "output": '{"case": {}}'}]
    # Data minimisation: the instructions forbid names; the request carries no id of its own.
    assert all("id" not in i for i in request["input"] if i.get("type") == "function_call")


async def test_a_reply_without_a_tool_call_is_nudged_once_then_gives_up() -> None:
    text = {"type": "message", "content": [{"type": "output_text", "text": "Done."}]}
    seen: list[dict[str, Any]] = []
    planner = OpenAIPlanner(
        _settings(), client=_client([{"output": [text]}, {"output": [text]}], seen), backoff=0
    )
    with pytest.raises(PlannerUnavailableError):
        await planner.next()
    assert "Continue by calling a tool" in json.dumps(seen[1]["input"])


async def test_an_unavailable_model_is_reported_for_fallback() -> None:
    planner = OpenAIPlanner(_settings(), client=_client([500, 500, 500], []), backoff=0)
    with pytest.raises(PlannerUnavailableError, match="provider_http_500"):
        await planner.next()
    refused = {"output": [{"type": "message", "content": [{"type": "refusal", "refusal": "no"}]}]}
    planner = OpenAIPlanner(_settings(), client=_client([refused], []), backoff=0)
    with pytest.raises(PlannerUnavailableError, match="model_refused"):
        await planner.next()
