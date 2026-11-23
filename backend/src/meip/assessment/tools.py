"""The agent's tools. Each has a Pydantic input schema, a typed output, validation, error
handling, a permission and an idempotency key; the agent loop records every call.

Tools gather facts from existing services (the profile, the checks) and call the
deterministic engines (policy, affordability, eligibility, evidence). **No tool lets the
agent change financial data, reviews, documents or approvals**: the only writes are evidence
requests, findings and escalations, all on the assessment itself.

Tool outputs use applicant labels ("Applicant 1"), never names, dates of birth or account
numbers: that is all the model ever sees (data minimisation, plan decision P10).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert

from meip.assessment import affordability as engine
from meip.assessment import eligibility as elig
from meip.assessment import evidence as gaps_mod
from meip.assessment.inputs import AssessmentInputs, IncomeLine
from meip.assessment.inputs import load as load_inputs
from meip.assessment.models import EvidenceRequest, RequestStatus
from meip.assessment.policy import Policy
from meip.assessment.schemas import (
    AffordabilityOut,
    AgentFindingOut,
    AssessmentApplicantOut,
    AssessmentResult,
    ChangeOut,
    CommitmentLineOut,
    CriterionOut,
    DiscrepancyOut,
    EligibilityOut,
    EscalationOut,
    GapOut,
    IncomeLineOut,
    LimitOut,
    PolicyOut,
    ReassessmentOut,
    ScenarioIn,
    ScenarioOut,
)
from meip.audit import service as audit
from meip.audit.service import AuditAction
from meip.profile.schemas import ProfileSnapshot
from meip.reconciliation import service as checks_service
from meip.reconciliation.schemas import ChecksOut
from meip.tenancy.context import OperationContext
from meip.tenancy.permissions import Permission


class ToolError(Exception):
    """A tool refused or failed: recorded with its code; the agent sees the message."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NoInput(ToolInput):
    pass


# --- state held by one run -------------------------------------------------------------------


@dataclass
class RunState:
    """What one assessment run has gathered. Facts are computed once and reused."""

    assessment_id: uuid.UUID
    case_id: uuid.UUID
    version: int
    policy: Policy
    today: date
    previous: dict[str, Any] | None = None  # previous assessment: id, version, outcome, result
    inputs: AssessmentInputs | None = None
    snapshot: ProfileSnapshot | None = None
    checks: ChecksOut | None = None
    affordability: engine.AffordabilityResult | None = None
    verified_max_minor: int | None = None
    eligibility: elig.Eligibility | None = None
    gaps: list[gaps_mod.Gap] | None = None
    scenarios: list[ScenarioOut] = field(default_factory=list)
    findings: list[AgentFindingOut] = field(default_factory=list)
    escalations: list[EscalationOut] = field(default_factory=list)
    requested: list[uuid.UUID] = field(default_factory=list)
    stage: str = "gathering"
    finished: AssessmentResult | None = None


@dataclass
class ToolCall:
    ctx: OperationContext
    state: RunState


# --- helpers ---------------------------------------------------------------------------------


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def idempotency_key(tool: str, args: dict[str, Any]) -> str:
    return hashlib.sha256(f"{tool}:{canonical(args)}".encode()).hexdigest()


def _gbp(minor: int | None) -> str:
    return "unknown" if minor is None else f"£{minor / 100:,.2f}"


async def _facts(call: ToolCall) -> tuple[AssessmentInputs, ProfileSnapshot, ChecksOut]:
    s = call.state
    if s.inputs is None or s.snapshot is None:
        s.inputs, s.snapshot = await load_inputs(call.ctx, s.case_id, s.policy, s.today)
    if s.checks is None:
        s.checks = s.snapshot.checks
    return s.inputs, s.snapshot, s.checks


def income_out(line: IncomeLine) -> IncomeLineOut:
    data = asdict(line)
    for drop in ("applicant_id", "start_date", "probation_end", "contract_end"):
        data.pop(drop, None)
    return IncomeLineOut(**data)


def affordability_out(r: engine.AffordabilityResult) -> AffordabilityOut:
    i = r.inputs
    return AffordabilityOut(
        policy_code=r.policy_code,
        gross_monthly_income_minor=i.gross_monthly_income_minor,
        net_monthly_income_minor=i.net_monthly_income_minor,
        commitments_monthly_minor=i.commitments_monthly_minor,
        living_costs_monthly_minor=i.living_costs_monthly_minor,
        living_costs_basis=i.living_costs_basis,
        property_value_minor=i.property_value_minor,
        loan_amount_minor=i.loan_amount_minor,
        deposit_minor=r.deposit_minor,
        min_deposit_minor=r.min_deposit_minor,
        rate_percent=i.rate_percent,
        stressed_rate_percent=r.stressed_rate_percent,
        term_months=i.term_months,
        disposable_before_mortgage_minor=r.disposable_before_mortgage_minor,
        max_affordable_monthly_repayment_minor=r.max_affordable_monthly_repayment_minor,
        limits=[LimitOut(**asdict(limit)) for limit in r.limits],
        max_mortgage_minor=r.max_mortgage_minor,
        binding_constraint=r.binding_constraint,
        monthly_repayment_minor=r.monthly_repayment_minor,
        stressed_monthly_repayment_minor=r.stressed_monthly_repayment_minor,
        total_repayable_minor=r.total_repayable_minor,
        disposable_after_mortgage_minor=r.disposable_after_mortgage_minor,
        dti_percent=r.dti_percent,
        ltv_percent=r.ltv_percent,
        lti=r.lti,
        requested_within_limits=r.requested_within_limits,
        shortfall_minor=r.shortfall_minor,
        risks=r.risks,
        assumptions=r.assumptions,
    )


def scenario_out(
    s: engine.Scenario, r: engine.AffordabilityResult, changed: list[str]
) -> ScenarioOut:
    i = r.inputs
    return ScenarioOut(
        label=s.label,
        changed=changed,
        loan_amount_minor=i.loan_amount_minor,
        deposit_minor=r.deposit_minor,
        term_months=i.term_months,
        rate_percent=i.rate_percent,
        monthly_repayment_minor=r.monthly_repayment_minor,
        stressed_monthly_repayment_minor=r.stressed_monthly_repayment_minor,
        total_repayable_minor=r.total_repayable_minor,
        max_mortgage_minor=r.max_mortgage_minor,
        binding_constraint=r.binding_constraint,
        ltv_percent=r.ltv_percent,
        lti=r.lti,
        dti_percent=r.dti_percent,
        within_limits=r.requested_within_limits,
        shortfall_minor=r.shortfall_minor,
        disposable_after_mortgage_minor=r.disposable_after_mortgage_minor,
    )


def base_inputs(
    inputs: AssessmentInputs, policy: Policy, *, verified_only: bool = False
) -> engine.AffordabilityInputs:
    lines = inputs.counted_income
    if verified_only:
        lines = [x for x in lines if x.verification == "verified"]
    return engine.AffordabilityInputs(
        gross_monthly_income_minor=sum(x.qualifying_gross_monthly_minor for x in lines),
        net_monthly_income_minor=sum(x.qualifying_net_monthly_minor or 0 for x in lines),
        commitments_monthly_minor=inputs.commitments_monthly_minor,
        living_costs_monthly_minor=inputs.living_costs_monthly_minor,
        living_costs_basis=inputs.living_costs_basis,
        property_value_minor=inputs.property_value_minor,
        loan_amount_minor=inputs.loan_amount_minor,
        rate_percent=(
            inputs.product_rate_percent
            if inputs.product_rate_percent is not None
            else policy.default_rate_percent
        ),
        term_months=inputs.term_months or policy.default_term_months,
        rate_basis="product" if inputs.product_rate_percent is not None else "policy_default",
    )


def _discrepancies(inputs: AssessmentInputs, checks: ChecksOut) -> list[DiscrepancyOut]:
    out: list[DiscrepancyOut] = []
    for f in checks.findings:
        if f.status != "discrepancy":
            continue
        analysis: list[str] = []
        a = f.amounts
        if "expected" in a and "actual" in a:
            diff = a["actual"] - a["expected"]
            pct = (
                (Decimal(abs(diff)) * 100 / Decimal(a["expected"])).quantize(Decimal("0.1"))
                if a["expected"]
                else None
            )
            if diff == 0:
                analysis.append(
                    f"The amount matches exactly ({_gbp(a['actual'])}): the issue isn't the "
                    "amount (see the message)."
                )
            else:
                analysis.append(
                    f"Expected {_gbp(a['expected'])}, found {_gbp(a['actual'])}: "
                    f"{'over' if diff > 0 else 'short'} by {_gbp(abs(diff))}"
                    + (f" ({pct} %)." if pct is not None else ".")
                )
        if f.match_outcome:
            analysis.append(
                {
                    "near": (
                        "A credit close to, but not exactly, the net pay arrived in the window."
                    ),
                    "none": "No credit near the net pay arrived in the window.",
                    "multiple": "Several credits could match; none can be relied on alone.",
                    "exact": "An exact credit arrived.",
                }[f.match_outcome]
            )
        if f.rule_code.startswith("YTD"):
            analysis.append("Year-to-date totals don't follow from the period figures.")
        if f.acknowledgement is not None:
            analysis.append("The broker has acknowledged it with a note.")
        out.append(
            DiscrepancyOut(
                key=f.key,
                rule_code=f.rule_code,
                title=f.title,
                message=f.message,
                applicant_label=inputs.label(f.applicant_id) if f.applicant_id else None,
                amounts=f.amounts,
                acknowledged=f.acknowledgement is not None,
                acknowledgement_note=f.acknowledgement.note if f.acknowledgement else None,
                analysis=analysis,
            )
        )
    return out


# --- tool implementations ---------------------------------------------------------------------


async def get_assessment_context(call: ToolCall, _args: NoInput) -> dict[str, Any]:
    inputs, snapshot, checks = await _facts(call)
    s = call.state
    docs: dict[str, dict[str, int]] = {}
    for d in snapshot.documents:
        docs.setdefault(d.document_type, {}).setdefault(d.status, 0)
        docs[d.document_type][d.status] += 1
    open_requests = list(
        await call.ctx.session.scalars(
            select(EvidenceRequest).where(
                EvidenceRequest.case_id == s.case_id,
                EvidenceRequest.status == RequestStatus.OPEN.value,
            )
        )
    )
    previous = None
    if s.previous:
        previous = {
            "version": s.previous["version"],
            "outcome": s.previous["outcome"],
            "finished_at": s.previous["finished_at"],
            "profile_changed_since": s.previous["profile_fingerprint"]
            != inputs.profile_fingerprint,
            "changes": [c.model_dump() for c in reassessment(s, inputs, checks).changes]
            if s.previous.get("result")
            else [],
        }
    return {
        "case": {
            "purpose": inputs.purpose,
            "property_use": inputs.property_use,
            "status": inputs.case_status,
            "property_value_minor": inputs.property_value_minor,
            "loan_amount_minor": inputs.loan_amount_minor,
            "term_months": inputs.term_months,
        },
        "applicants": [
            {"label": a.label, "role": a.party_role, "primary": a.is_primary}
            for a in inputs.applicants
        ],
        "profile": {
            "approved": inputs.profile_approved,
            "fingerprint": inputs.profile_fingerprint[:12],
        },
        "documents": docs,
        "income_sources": len(inputs.income),
        "commitments": len(inputs.commitments),
        "discrepancies_open": sum(
            1 for f in checks.findings if f.status == "discrepancy" and f.acknowledgement is None
        ),
        "open_evidence_requests": [
            {"gap_code": r.gap_code, "title": r.title, "requested_by": r.requested_by}
            for r in open_requests
        ],
        "policy": {"code": s.policy.code, "name": s.policy.name},
        "previous_assessment": previous,
    }


class ApplicantFilter(ToolInput):
    applicant: str | None = Field(
        default=None, description='An applicant label, e.g. "Applicant 1"; all if omitted'
    )


async def get_applicant_profile(call: ToolCall, args: ApplicantFilter) -> dict[str, Any]:
    inputs, _, _ = await _facts(call)
    labels = {a.label for a in inputs.applicants}
    if args.applicant is not None and args.applicant not in labels:
        raise ToolError("unknown_applicant", f"No applicant is labelled {args.applicant!r}.")
    income = [
        income_out(x).model_dump(mode="json")
        for x in inputs.income
        if args.applicant is None or x.applicant_label == args.applicant
    ]
    return {
        "applicants": [
            AssessmentApplicantOut(**asdict(a)).model_dump(mode="json", exclude={"applicant_id"})
            for a in inputs.applicants
            if args.applicant in (None, a.label)
        ],
        "income": income,
        "commitments": [
            CommitmentLineOut(**asdict(c)).model_dump(mode="json") for c in inputs.commitments
        ],
        "living_costs_monthly_minor": inputs.living_costs_monthly_minor,
        "living_costs_basis": inputs.living_costs_basis,
        "notes": inputs.notes,
    }


async def get_financial_evidence(call: ToolCall, _args: NoInput) -> dict[str, Any]:
    inputs, snapshot, _ = await _facts(call)
    return {
        "documents": [
            {
                "type": d.document_type,
                "status": d.status,
                "applicant": inputs.label(d.applicant_id),
            }
            for d in snapshot.documents
        ],
        "bank_accounts": [
            {
                "ending": a.account_ending[-2:] if a.account_ending else None,
                "holders": len(a.holders),
            }
            for a in snapshot.bank_accounts
        ],
        "support": [
            {
                "income_source_id": str(line.income_source_id),
                "verification": line.verification,
                "periods_evidenced": line.periods_evidenced,
                "salary_received_for": line.corroborated_periods,
            }
            for line in inputs.income
        ],
    }


async def identify_evidence_gaps(call: ToolCall, _args: NoInput) -> dict[str, Any]:
    inputs, _, checks = await _facts(call)
    s = call.state
    s.gaps = gaps_mod.identify(s.policy, inputs, checks, s.today)
    return {
        "gaps": [
            GapOut(**asdict(g)).model_dump(mode="json", exclude={"applicant_id"}) for g in s.gaps
        ]
    }


async def reconcile_financial_evidence(call: ToolCall, _args: NoInput) -> dict[str, Any]:
    inputs, _, _ = await _facts(call)
    # Re-run the existing checks now (read-only), so the latest reviewed data is used.
    call.state.checks = await checks_service.get_checks(call.ctx, call.state.case_id)
    checks = call.state.checks
    return {
        "rule_sets": checks.ruleset_versions,
        "passed": sum(1 for f in checks.findings if f.status == "passed"),
        "not_run": [
            {"title": f.title, "reason": f.message}
            for f in checks.findings
            if f.status == "not_run"
        ],
        "discrepancies": [d.model_dump(mode="json") for d in _discrepancies(inputs, checks)],
    }


async def validate_income(call: ToolCall, _args: NoInput) -> dict[str, Any]:
    inputs, _, _ = await _facts(call)
    return {
        "sources": [
            {
                "income_source_id": str(x.income_source_id),
                "applicant": x.applicant_label,
                "kind": x.kind,
                "verification": x.verification,
                "recurring": x.recurring,
                "counted": x.counted,
                "gross_monthly_minor": x.gross_monthly_minor,
                "qualifying_gross_monthly_minor": x.qualifying_gross_monthly_minor,
                "qualifying_net_monthly_minor": x.qualifying_net_monthly_minor,
                "declared_gross_monthly_minor": x.declared_gross_monthly_minor,
                "notes": x.notes,
            }
            for x in inputs.income
        ],
        "qualifying_gross_monthly_minor": inputs.gross_monthly_minor,
        "qualifying_net_monthly_minor": inputs.net_monthly_minor,
        "excluded": [
            {
                "income_source_id": str(x.income_source_id),
                "verification": x.verification,
                "gross_monthly_minor": x.gross_monthly_minor,
            }
            for x in inputs.excluded_income
        ],
        "double_counting": (
            "Payroll income uses total gross pay only; components only discount variable "
            "pay; declared figures aren't added to documented ones."
        ),
    }


async def calculate_monthly_commitments(call: ToolCall, _args: NoInput) -> dict[str, Any]:
    inputs, _, _ = await _facts(call)
    return {
        "commitments": [
            CommitmentLineOut(**asdict(c)).model_dump(mode="json") for c in inputs.commitments
        ],
        "commitments_monthly_minor": inputs.commitments_monthly_minor,
        "living_costs_monthly_minor": inputs.living_costs_monthly_minor,
        "living_costs_basis": inputs.living_costs_basis,
    }


async def calculate_disposable_income(call: ToolCall, _args: NoInput) -> dict[str, Any]:
    inputs, _, _ = await _facts(call)
    disposable = (
        inputs.net_monthly_minor
        - inputs.commitments_monthly_minor
        - inputs.living_costs_monthly_minor
    )
    return {
        "net_monthly_income_minor": inputs.net_monthly_minor,
        "commitments_monthly_minor": inputs.commitments_monthly_minor,
        "living_costs_monthly_minor": inputs.living_costs_monthly_minor,
        "disposable_before_mortgage_minor": disposable,
        "net_income_unknown_for": [
            str(x.income_source_id)
            for x in inputs.counted_income
            if x.qualifying_net_monthly_minor is None
        ],
    }


async def _ensure_affordability(call: ToolCall) -> engine.AffordabilityResult | None:
    inputs, _, _ = await _facts(call)
    s = call.state
    if s.affordability is None and inputs.counted_income:
        s.affordability = engine.assess(s.policy, base_inputs(inputs, s.policy))
        if any(x.verification == "verified" for x in inputs.counted_income):
            s.verified_max_minor = engine.assess(
                s.policy, base_inputs(inputs, s.policy, verified_only=True)
            ).max_mortgage_minor
        else:
            s.verified_max_minor = 0
    return s.affordability


async def calculate_affordability(call: ToolCall, _args: NoInput) -> dict[str, Any]:
    result = await _ensure_affordability(call)
    if result is None:
        raise ToolError(
            "no_counted_income", "No income is counted yet, so affordability can't be calculated."
        )
    return affordability_out(result).model_dump(mode="json")


async def _ensure_eligibility(call: ToolCall) -> elig.Eligibility:
    inputs, _, checks = await _facts(call)
    s = call.state
    result = await _ensure_affordability(call)
    if s.gaps is None:
        s.gaps = gaps_mod.identify(s.policy, inputs, checks, s.today)
    outstanding = [g.title for g in s.gaps if g.affects_outcome and g.category == "document"]
    s.eligibility = elig.evaluate(
        s.policy,
        inputs,
        result,
        open_discrepancies=[
            f.key
            for f in checks.findings
            if f.status == "discrepancy" and f.acknowledgement is None
        ],
        acknowledged_discrepancies=[
            f.key
            for f in checks.findings
            if f.status == "discrepancy" and f.acknowledgement is not None
        ],
        today=s.today,
        verified_max_minor=s.verified_max_minor,
        outstanding_evidence=outstanding,
    )
    return s.eligibility


def eligibility_out(e: elig.Eligibility) -> EligibilityOut:
    return EligibilityOut(
        policy_code=e.policy_code,
        outcome=e.outcome,
        outcome_label=e.outcome_label,
        criteria=[CriterionOut(**asdict(c)) for c in e.criteria],
        reasons=e.reasons,
        unmet=e.unmet,
        outstanding=e.outstanding,
        indicative_range_minor=list(e.indicative_range_minor) if e.indicative_range_minor else None,
    )


async def evaluate_eligibility(call: ToolCall, _args: NoInput) -> dict[str, Any]:
    return eligibility_out(await _ensure_eligibility(call)).model_dump(mode="json")


class ScenariosArgs(ToolInput):
    scenarios: list[ScenarioIn] = Field(
        default_factory=list,
        max_length=6,
        description="Variations to compare. Omit to use the standard alternatives (borrow the "
        "maximum, a larger deposit, a longer term, a lower rate).",
    )


def scenarios_for(
    policy: Policy,
    inputs: AssessmentInputs,
    base: engine.AffordabilityResult,
    requested: list[ScenarioIn],
) -> list[ScenarioOut]:
    if requested:
        excluded_gross = sum(x.gross_monthly_minor or 0 for x in inputs.excluded_income)
        excluded_net = sum(x.net_monthly_minor or 0 for x in inputs.excluded_income)
        chosen = [
            engine.Scenario(
                label=r.label,
                loan_amount_minor=r.loan_amount_minor,
                deposit_minor=r.deposit_minor,
                term_months=r.term_months,
                rate_percent=r.rate_percent,
                extra_gross_monthly_minor=excluded_gross if r.include_unverified_income else 0,
                extra_net_monthly_minor=excluded_net if r.include_unverified_income else 0,
            )
            for r in requested
        ]
    else:
        chosen = engine.default_scenarios(policy, base)
    return [scenario_out(s, r, c) for s, r, c in engine.run_scenarios(policy, base.inputs, chosen)]


async def simulate_mortgage_scenarios(call: ToolCall, args: ScenariosArgs) -> dict[str, Any]:
    inputs, _, _ = await _facts(call)
    base = await _ensure_affordability(call)
    if base is None:
        raise ToolError("no_counted_income", "No income is counted yet, so scenarios can't be run.")
    out = scenarios_for(call.state.policy, inputs, base, args.scenarios)
    call.state.scenarios = out
    return {
        "note": (
            "Hypothetical: each scenario changes the named assumptions only; the base "
            "assessment is unchanged."
        ),
        "base_max_mortgage_minor": base.max_mortgage_minor,
        "scenarios": [s.model_dump(mode="json") for s in out],
    }


class EvidenceRequestArgs(ToolInput):
    gap_codes: list[str] = Field(
        min_length=1,
        max_length=20,
        description="Codes of current evidence gaps (from identify_evidence_gaps)",
    )


async def generate_evidence_request(call: ToolCall, args: EvidenceRequestArgs) -> dict[str, Any]:
    call.ctx.require(Permission.ASSESSMENT_RUN)
    s = call.state
    if s.gaps is None:
        inputs, _, checks = await _facts(call)
        s.gaps = gaps_mod.identify(s.policy, inputs, checks, s.today)
    current = {g.code: g for g in s.gaps}
    unknown = [c for c in args.gap_codes if c not in current]
    if unknown:
        raise ToolError(
            "unknown_gap",
            f"Not current evidence gaps: {', '.join(unknown)}. Request only identified gaps.",
        )
    actions = [c for c in args.gap_codes if current[c].category == "action"]
    if actions:
        raise ToolError(
            "not_a_request",
            f"These are broker actions, not evidence to request: {', '.join(actions)}. "
            f"Escalate them instead.",
        )
    created, already = [], []
    for code in args.gap_codes:
        g = current[code]
        stmt = (
            insert(EvidenceRequest)
            .values(
                id=uuid.uuid4(),
                tenant_id=call.ctx.tenant_id,
                case_id=s.case_id,
                assessment_id=s.assessment_id,
                applicant_id=uuid.UUID(g.applicant_id) if g.applicant_id else None,
                gap_code=g.code,
                category=g.category,
                title=g.title[:300],
                reason=g.reason[:1000],
                document_type=g.document_type,
                months=g.months or None,
                supported_on_platform=g.supported_on_platform,
                requested_by="agent",
                status=RequestStatus.OPEN.value,
            )
            .on_conflict_do_nothing(
                index_elements=["case_id", "gap_code"], index_where=text("status = 'open'")
            )
            .returning(EvidenceRequest.id)
        )
        new_id = (await call.ctx.session.execute(stmt)).scalar_one_or_none()
        if new_id is None:
            already.append(code)
            continue
        created.append(code)
        s.requested.append(new_id)
        change = audit.ChangeSet()
        for name, value in (
            ("assessment_id", s.assessment_id),
            ("gap_code", g.code),
            ("category", g.category),
        ):
            change.note(name, value)
        await audit.record(
            call.ctx.session,
            tenant_id=call.ctx.tenant_id,
            actor=call.ctx.actor,
            action=AuditAction.EVIDENCE_REQUESTED,
            entity_type="evidence_request",
            entity_id=new_id,
            case_id=s.case_id,
            change_set=change,
        )
    return {"created": created, "already_open": already}


class FindingArgs(ToolInput):
    title: str = Field(min_length=1, max_length=200)
    detail: str = Field(min_length=1, max_length=1000)
    severity: Literal["info", "warning", "critical"]
    refs: list[str] = Field(
        default_factory=list,
        max_length=20,
        description="Finding keys, income source ids or gap codes it relies on",
    )


async def record_finding(call: ToolCall, args: FindingArgs) -> dict[str, Any]:
    call.state.findings.append(AgentFindingOut(**args.model_dump()))
    return {"recorded": len(call.state.findings)}


class EscalationArgs(ToolInput):
    reason: str = Field(min_length=1, max_length=1000)
    refs: list[str] = Field(default_factory=list, max_length=20)


async def escalate_to_broker(call: ToolCall, args: EscalationArgs) -> dict[str, Any]:
    call.ctx.require(Permission.ASSESSMENT_RUN)
    call.state.escalations.append(EscalationOut(**args.model_dump()))
    change = audit.ChangeSet()
    change.note("assessment_id", call.state.assessment_id)
    change.note("reason", args.reason)
    await audit.record(
        call.ctx.session,
        tenant_id=call.ctx.tenant_id,
        actor=call.ctx.actor,
        action=AuditAction.ASSESSMENT_ESCALATED,
        entity_type="assessment",
        entity_id=call.state.assessment_id,
        case_id=call.state.case_id,
        change_set=change,
    )
    return {"escalations": len(call.state.escalations)}


class StatusArgs(ToolInput):
    stage: Literal[
        "gathering",
        "investigating",
        "assessing",
        "awaiting_evidence",
        "awaiting_broker",
        "ready_for_review",
    ]
    note: str = Field(default="", max_length=300)


async def update_assessment_status(call: ToolCall, args: StatusArgs) -> dict[str, Any]:
    call.state.stage = args.stage
    return {"stage": args.stage}


class ReportArgs(ToolInput):
    summary: str = Field(
        min_length=1,
        max_length=3000,
        description=(
            "Plain-English explanation of the findings. Quote figures only from tool outputs."
        ),
    )
    recommendations: list[str] = Field(default_factory=list, max_length=8)


def reassessment(s: RunState, inputs: AssessmentInputs, checks: ChecksOut) -> ReassessmentOut:
    """What changed since the previous assessment, and which earlier results it affects."""
    prev = s.previous or {}
    result = prev.get("result") or {}
    changes: list[ChangeOut] = []
    affected: list[str] = []
    before = {i["income_source_id"]: i for i in result.get("income", [])}
    for line in inputs.income:
        old = before.pop(str(line.income_source_id), None)
        if old is None:
            changes.append(
                ChangeOut(
                    kind="income_added",
                    detail=f"New {line.kind} income for {line.applicant_label}.",
                )
            )
            affected.append("income and every income-based result")
            continue
        if old["verification"] != line.verification:
            changes.append(
                ChangeOut(
                    kind="verification",
                    detail=f"{line.applicant_label} {line.kind} income: {old['verification']} → "
                    f"{line.verification}.",
                )
            )
            affected.append("income evidence criteria and the indicative range")
        added = sorted(set(line.periods_evidenced) - set(old.get("periods_evidenced", [])))
        if added:
            changes.append(
                ChangeOut(
                    kind="new_evidence",
                    detail=f"{line.applicant_label}: payslips reviewed for {', '.join(added)}.",
                )
            )
            affected.append("payslip evidence gaps")
        new_corr = sorted(set(line.corroborated_periods) - set(old.get("corroborated_periods", [])))
        if new_corr:
            changes.append(
                ChangeOut(
                    kind="new_evidence",
                    detail=(
                        f"{line.applicant_label}: salary receipt shown for {', '.join(new_corr)}."
                    ),
                )
            )
            affected.append("statement evidence gaps and verification")
        if old.get("qualifying_gross_monthly_minor") != line.qualifying_gross_monthly_minor:
            changes.append(
                ChangeOut(
                    kind="income_changed",
                    detail=f"{line.applicant_label} qualifying gross "
                    f"{_gbp(old.get('qualifying_gross_monthly_minor'))} → "
                    f"{_gbp(line.qualifying_gross_monthly_minor)} a month.",
                )
            )
            affected.append("affordability, loan to income and the outcome")
    for old in before.values():
        changes.append(
            ChangeOut(kind="income_removed", detail=f"{old['kind']} income no longer on the case.")
        )
        affected.append("income and every income-based result")
    case_before = result.get("case", {})
    for key in ("loan_amount_minor", "property_value_minor", "term_months", "property_use"):
        now = getattr(inputs, key, None)
        if case_before.get(key) != now:
            changes.append(
                ChangeOut(
                    kind="case", detail=f"{key.replace('_minor', '').replace('_', ' ')} changed."
                )
            )
            affected.append("affordability, loan to value and the outcome")
    old_disc = {d["key"]: d for d in result.get("discrepancies", [])}
    now_disc = {f.key: f for f in checks.findings if f.status == "discrepancy"}
    for key in old_disc.keys() - now_disc.keys():
        changes.append(
            ChangeOut(
                kind="discrepancy_resolved",
                detail=f"No longer a discrepancy: {old_disc[key]['title']}.",
            )
        )
        affected.append(f"finding {key[:12]}")
    for key in now_disc.keys() - old_disc.keys():
        changes.append(
            ChangeOut(kind="discrepancy_new", detail=f"New discrepancy: {now_disc[key].title}.")
        )
        affected.append("evidence consistency criterion")
    for key in now_disc.keys() & old_disc.keys():
        if now_disc[key].acknowledgement is not None and not old_disc[key]["acknowledged"]:
            changes.append(
                ChangeOut(
                    kind="discrepancy_acknowledged",
                    detail=f"Acknowledged by the broker: {now_disc[key].title}.",
                )
            )
            affected.append("evidence consistency criterion")
    if result.get("commitments") is not None:
        old_c = sum(c.get("monthly_minor") or 0 for c in result["commitments"] if c.get("counted"))
        if old_c != inputs.commitments_monthly_minor:
            changes.append(
                ChangeOut(
                    kind="commitments",
                    detail=f"Monthly commitments {_gbp(old_c)} → "
                    f"{_gbp(inputs.commitments_monthly_minor)}.",
                )
            )
            affected.append("disposable income, DTI and affordability")
    return ReassessmentOut(
        previous_assessment_id=prev["id"],
        previous_version=prev["version"],
        previous_outcome=prev.get("outcome"),
        changes=changes,
        affected=sorted(set(affected)),
    )


async def generate_assessment_report(call: ToolCall, args: ReportArgs) -> dict[str, Any]:
    inputs, _, checks = await _facts(call)
    s = call.state
    e = await _ensure_eligibility(call)
    if s.gaps is None:
        s.gaps = gaps_mod.identify(s.policy, inputs, checks, s.today)
    limitations = [r for r in e.reasons if "Not assessed" in r]
    s.finished = AssessmentResult(
        policy=PolicyOut(
            code=s.policy.code,
            name=s.policy.name,
            description=s.policy.description,
            parameters=s.policy.parameters(),
        ),
        case={
            "purpose": inputs.purpose,
            "property_use": inputs.property_use,
            "property_value_minor": inputs.property_value_minor,
            "loan_amount_minor": inputs.loan_amount_minor,
            "term_months": inputs.term_months,
        },
        applicants=[AssessmentApplicantOut(**asdict(a)) for a in inputs.applicants],
        income=[income_out(x) for x in inputs.income],
        commitments=[CommitmentLineOut(**asdict(c)) for c in inputs.commitments],
        affordability=affordability_out(s.affordability) if s.affordability else None,
        eligibility=eligibility_out(e),
        scenarios=s.scenarios,
        evidence_gaps=[GapOut(**asdict(g)) for g in s.gaps],
        discrepancies=_discrepancies(inputs, checks),
        findings=s.findings,
        escalations=s.escalations,
        evidence_request_ids=s.requested,
        reassessment=reassessment(s, inputs, checks)
        if s.previous and s.previous.get("result")
        else None,
        summary=args.summary,
        recommendations=args.recommendations,
        limitations=limitations,
        profile_fingerprint=inputs.profile_fingerprint,
        profile_approved=inputs.profile_approved,
    )
    return {"outcome": e.outcome, "outcome_label": e.outcome_label, "finished": True}


# --- registry ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_model: type[ToolInput]
    handler: Callable[[ToolCall, Any], Awaitable[dict[str, Any]]]
    writes: bool = False


TOOLS: dict[str, Tool] = {
    t.name: t
    for t in (
        Tool(
            "get_assessment_context",
            "The case, applicants (by label), documents, open evidence requests, the policy, "
            "and the previous assessment with what changed since. Call first.",
            NoInput,
            get_assessment_context,
        ),
        Tool(
            "get_applicant_profile",
            "Each applicant's income sources (with verification status), commitments and "
            "living costs, from the broker-reviewed financial profile.",
            ApplicantFilter,
            get_applicant_profile,
        ),
        Tool(
            "get_financial_evidence",
            "Documents by type and status, bank accounts, and for each income source the "
            "periods evidenced and the months the salary was seen arriving.",
            NoInput,
            get_financial_evidence,
        ),
        Tool(
            "identify_evidence_gaps",
            "What evidence or information is required by the policy and the applicant's "
            "circumstances but missing, with reasons.",
            NoInput,
            identify_evidence_gaps,
        ),
        Tool(
            "reconcile_financial_evidence",
            "Run the cross-document checks (payslips and statements) and return "
            "discrepancies with a factual comparison of the evidence.",
            NoInput,
            reconcile_financial_evidence,
        ),
        Tool(
            "validate_income",
            "Which income counts under the policy: verification status, recurring or not, "
            "qualifying gross and net monthly income, exclusions, and how double counting is "
            "avoided.",
            NoInput,
            validate_income,
        ),
        Tool(
            "calculate_monthly_commitments",
            "Existing commitments counted (monthly) and living costs (declared or policy default).",
            NoInput,
            calculate_monthly_commitments,
        ),
        Tool(
            "calculate_disposable_income",
            "Net income less commitments and living costs, before the mortgage.",
            NoInput,
            calculate_disposable_income,
        ),
        Tool(
            "calculate_affordability",
            "Deterministic affordability for the requested loan: repayments (actual and "
            "stressed), DTI, LTV, LTI, the affordability, income-multiple and LTV limits, "
            "maximum mortgage, shortfall and risks.",
            NoInput,
            calculate_affordability,
        ),
        Tool(
            "evaluate_eligibility",
            "Indicative pre-qualification against the policy: each criterion met, not met, "
            "unknown or not assessed, and the outcome. The outcome comes only from this tool.",
            NoInput,
            evaluate_eligibility,
        ),
        Tool(
            "simulate_mortgage_scenarios",
            "Hypothetical alternatives (loan, deposit, term, rate, counting unverified "
            "income as if verified). Never changes the base assessment.",
            ScenariosArgs,
            simulate_mortgage_scenarios,
        ),
        Tool(
            "generate_evidence_request",
            "Open evidence requests for identified document or information gaps (by gap "
            "code). Already-open requests are not duplicated.",
            EvidenceRequestArgs,
            generate_evidence_request,
            writes=True,
        ),
        Tool(
            "record_finding",
            "Record a finding from your investigation, with the evidence it relies on.",
            FindingArgs,
            record_finding,
        ),
        Tool(
            "escalate_to_broker",
            "Escalate something only the broker can resolve (an unresolved discrepancy, a "
            "failed criterion, a judgement call).",
            EscalationArgs,
            escalate_to_broker,
            writes=True,
        ),
        Tool(
            "update_assessment_status",
            "Record the stage the assessment has reached.",
            StatusArgs,
            update_assessment_status,
        ),
        Tool(
            "generate_assessment_report",
            "Finish: compile the evidence-backed assessment with your explanation and "
            "recommendations. The outcome and figures come from the tools.",
            ReportArgs,
            generate_assessment_report,
        ),
    )
}


def parse_args(tool: Tool, raw: dict[str, Any]) -> ToolInput:
    try:
        return tool.input_model.model_validate(raw)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or 'input'}: {err['msg']}"
            for err in exc.errors()[:5]
        )
        raise ToolError("invalid_input", f"Invalid input for {tool.name}: {problems}") from None


def json_schema(tool: Tool) -> dict[str, Any]:
    schema = tool.input_model.model_json_schema()
    schema.pop("title", None)
    schema.setdefault("properties", {})
    schema["properties"]["why"] = {
        "type": "string",
        "description": "One short sentence, shown to the broker: why you're calling this tool now.",
    }
    schema["additionalProperties"] = False
    return schema
