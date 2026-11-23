"""Assessment services: start a run, read results, broker decisions, evidence requests and
the scenario calculator. The agent itself runs in :mod:`meip.assessment.agent`.

* **Duplicate protection:** one queued or running assessment per case (database index). If
  nothing changed since the latest completed assessment (profile fingerprint, case inputs and
  policy), starting returns that assessment unless the broker forces a re-run.
* **Stale runs:** a run queued or running for more than 15 minutes (its process stopped) is
  marked failed, so a new one can start.
* **Versioning:** each run is a new version; a finished one never changes.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from meip.assessment import affordability as engine
from meip.assessment.inputs import load as load_inputs
from meip.assessment.models import (
    Assessment,
    AssessmentDecision,
    AssessmentStatus,
    AssessmentStep,
    DecisionKind,
    EvidenceRequest,
    RequestStatus,
)
from meip.assessment.policy import DEFAULT_POLICY, POLICIES, get_policy
from meip.assessment.schemas import (
    AssessmentOut,
    AssessmentResult,
    AssessmentSummaryOut,
    CommitmentLineOut,
    DecisionIn,
    DecisionOut,
    EvidenceRequestOut,
    PolicyOut,
    PositionOut,
    ScenariosIn,
    ScenariosOut,
    StartedOut,
    StartIn,
    StepOut,
)
from meip.assessment.tools import (
    affordability_out,
    base_inputs,
    canonical,
    income_out,
    scenarios_for,
)
from meip.audit import service as audit
from meip.audit.service import AuditAction
from meip.cases.models import Case, CaseStatus
from meip.cases.service import get_case
from meip.core.db import utcnow
from meip.core.errors import BusinessRuleError, ConflictError, NotFoundError
from meip.profile import service as profile_service
from meip.tenancy.context import OperationContext
from meip.tenancy.permissions import Permission

STALE_AFTER = timedelta(minutes=15)
# Decisions that complete the journey; requesting information or escalating keep it open.
FINAL_DECISIONS = frozenset({DecisionKind.ACCEPT.value, DecisionKind.OVERRIDE.value})


class AssessmentInProgressError(ConflictError):
    error_type = "assessment-in-progress"
    title = "An assessment is already running for this case"


class AssessmentBlockedError(ConflictError):
    error_type = "assessment-blocked"
    title = "The assessment can't run yet"


def blockers(case: Case) -> list[dict[str, str]]:
    """What must be true before an assessment can run (enforced here, not by the screens):
    an approved financial profile, and the loan details it's assessed for."""
    out: list[dict[str, str]] = []
    if case.status != CaseStatus.VERIFIED.value or case.approval_id is None:
        out.append(
            {
                "code": "profile_not_approved",
                "message": "Approve the financial profile first: the assessment uses the "
                "approved figures.",
            }
        )
    missing = [
        name
        for name, value in (
            ("property value", case.property_value_minor),
            ("loan amount", case.loan_amount_minor),
            ("term", case.term_months),
        )
        if not value
    ]
    if missing:
        out.append(
            {
                "code": "loan_details_missing",
                "message": f"Add the {', '.join(missing)} to the case.",
            }
        )
    return out


def policy_for(case: Case, requested: str | None) -> str:
    return requested or case.policy_code or DEFAULT_POLICY


class AssessmentNotFinishedError(ConflictError):
    error_type = "assessment-not-finished"
    title = "The assessment hasn't finished"


def list_policies() -> list[PolicyOut]:
    return [
        PolicyOut(code=p.code, name=p.name, description=p.description, parameters=p.parameters())
        for p in POLICIES.values()
    ]


async def current_inputs_sha256(ctx: OperationContext, case_id: uuid.UUID, policy_code: str) -> str:
    """What an assessment depends on: the profile (as approved or not) and the case inputs."""
    built = await profile_service.snapshot(ctx, case_id)
    approval = await profile_service.approval_status(ctx, built.case)
    c = built.case
    return profile_service.sha256_of(
        canonical(
            {
                "profile": built.fingerprint,
                "approved": approval.status,
                "policy": policy_code,
                "loan": c.loan_amount_minor,
                "property": c.property_value_minor,
                "term": c.term_months,
                "use": c.property_use,
                "rate": str(c.product_rate_percent) if c.product_rate_percent is not None else None,
                "approval": str(c.approval_id) if c.approval_id else None,
            }
        )
    )


async def _expire_stale(ctx: OperationContext, case_id: uuid.UUID) -> None:
    for a in await ctx.session.scalars(
        select(Assessment).where(
            Assessment.case_id == case_id,
            Assessment.status.in_([AssessmentStatus.QUEUED.value, AssessmentStatus.RUNNING.value]),
        )
    ):
        if (a.started_at or a.created_at) < utcnow() - STALE_AFTER:
            a.status = AssessmentStatus.FAILED.value
            a.error_code = "stale"
            a.finished_at = utcnow()
    await ctx.session.flush()


async def start(
    ctx: OperationContext, case_id: uuid.UUID, body: StartIn
) -> tuple[StartedOut, bool]:
    """Queue an assessment. Returns the output and whether a run must be started.

    Refused (``assessment-blocked``) unless the financial profile is approved and the loan
    details are on the case. The assessment records the approval it assesses."""
    case = await get_case(ctx, case_id, for_update=True)
    policy_code = policy_for(case, body.policy_code)
    get_policy(policy_code)  # 404 for an unknown policy
    blocked = blockers(case)
    if blocked:
        raise AssessmentBlockedError(
            " ".join(b["message"] for b in blocked), extra={"blockers": blocked}
        )
    await _expire_stale(ctx, case_id)
    active = await ctx.session.scalar(
        select(Assessment).where(
            Assessment.case_id == case_id,
            Assessment.status.in_([AssessmentStatus.QUEUED.value, AssessmentStatus.RUNNING.value]),
        )
    )
    if active is not None:
        raise AssessmentInProgressError(extra={"assessment_id": str(active.id)})
    inputs_sha = await current_inputs_sha256(ctx, case_id, policy_code)
    latest = await ctx.session.scalar(
        select(Assessment)
        .where(Assessment.case_id == case_id, Assessment.status == AssessmentStatus.COMPLETED.value)
        .order_by(Assessment.version.desc())
        .limit(1)
    )
    if latest is not None and not body.force and latest.inputs_sha256 == inputs_sha:
        return StartedOut(assessment=await _summary(ctx, latest, inputs_sha), reused=True), False
    version = (
        await ctx.session.scalar(
            select(func.max(Assessment.version)).where(Assessment.case_id == case_id)
        )
        or 0
    ) + 1
    a = Assessment(
        tenant_id=ctx.tenant_id,
        case_id=case_id,
        version=version,
        status=AssessmentStatus.QUEUED.value,
        policy_code=policy_code,
        planner="pending",
        trigger="reassessment" if latest is not None else "initial",
        inputs_sha256=inputs_sha,
        previous_assessment_id=latest.id if latest else None,
        started_by_user_id=ctx.actor.user_id,
        step_count=0,
        approval_id=case.approval_id,
    )
    ctx.session.add(a)
    try:
        await ctx.session.flush()
    except IntegrityError:
        raise AssessmentInProgressError() from None
    change = audit.ChangeSet()
    for name, value in (
        ("assessment_id", a.id),
        ("assessment_version", version),
        ("policy_code", policy_code),
        ("trigger", a.trigger),
        ("approval_id", case.approval_id),
    ):
        change.note(name, value)
    await audit.record(
        ctx.session,
        tenant_id=ctx.tenant_id,
        actor=ctx.actor,
        action=AuditAction.ASSESSMENT_STARTED,
        entity_type="assessment",
        entity_id=a.id,
        case_id=case_id,
        change_set=change,
    )
    return StartedOut(assessment=await _summary(ctx, a, inputs_sha), reused=False), True


async def _decisions(ctx: OperationContext, assessment_id: uuid.UUID) -> list[AssessmentDecision]:
    return list(
        await ctx.session.scalars(
            select(AssessmentDecision)
            .where(AssessmentDecision.assessment_id == assessment_id)
            .order_by(AssessmentDecision.created_at, AssessmentDecision.id)
        )
    )


async def _summary(
    ctx: OperationContext, a: Assessment, current_sha: str | None
) -> AssessmentSummaryOut:
    decisions = await _decisions(ctx, a.id)
    override = next(
        (d for d in reversed(decisions) if d.decision == DecisionKind.OVERRIDE.value), None
    )
    names = await profile_service._members(
        ctx, {a.started_by_user_id} if a.started_by_user_id else set()
    )
    return AssessmentSummaryOut(
        id=a.id,
        case_id=a.case_id,
        version=a.version,
        status=a.status,
        outcome=a.outcome,
        policy_code=a.policy_code,
        planner=a.planner,
        model=a.model,
        trigger=a.trigger,
        profile_fingerprint=a.profile_fingerprint,
        previous_assessment_id=a.previous_assessment_id,
        approval_id=a.approval_id,
        started_by=names.get(a.started_by_user_id) if a.started_by_user_id else None,
        created_at=a.created_at,
        started_at=a.started_at,
        finished_at=a.finished_at,
        error_code=a.error_code,
        step_count=a.step_count,
        stale=a.status == AssessmentStatus.COMPLETED.value
        and current_sha is not None
        and current_sha != a.inputs_sha256,
        final_outcome=(override.override_outcome if override else a.outcome),
        decision=decisions[-1].decision if decisions else None,
        decided=any(d.decision in FINAL_DECISIONS for d in decisions),
    )


async def start_after_approval(
    ctx: OperationContext, case_id: uuid.UUID
) -> tuple[StartedOut, bool] | None:
    """Guided journey: approving the profile starts the assessment, in the same transaction.
    Returns None when it can't start yet (no loan details, or the actor may not run it); the
    journey then shows what's needed."""
    if not ctx.actor.can(Permission.ASSESSMENT_RUN):
        return None
    case = await get_case(ctx, case_id)
    if blockers(case):
        return None
    try:
        return await start(ctx, case_id, StartIn(force=True))
    except AssessmentInProgressError:
        return None


async def position(ctx: OperationContext, case_id: uuid.UUID, today: date) -> PositionOut:
    case = await get_case(ctx, case_id)
    policy = get_policy(policy_for(case, None))
    inputs, _ = await load_inputs(ctx, case_id, policy, today)
    return PositionOut(
        policy_code=policy.code,
        income=[income_out(x) for x in inputs.income],
        commitments=[CommitmentLineOut(**asdict(c)) for c in inputs.commitments],
        gross_monthly_minor=inputs.gross_monthly_minor,
        net_monthly_minor=inputs.net_monthly_minor,
        verified_gross_monthly_minor=sum(
            x.qualifying_gross_monthly_minor
            for x in inputs.counted_income
            if x.verification == "verified"
        ),
        commitments_monthly_minor=inputs.commitments_monthly_minor,
        living_costs_monthly_minor=inputs.living_costs_monthly_minor,
        living_costs_basis=inputs.living_costs_basis,
        property_value_minor=inputs.property_value_minor,
        loan_amount_minor=inputs.loan_amount_minor,
        deposit_minor=(
            inputs.property_value_minor - inputs.loan_amount_minor
            if inputs.property_value_minor is not None and inputs.loan_amount_minor is not None
            else None
        ),
        product_rate_percent=inputs.product_rate_percent,
        term_months=case.term_months,
        notes=inputs.notes,
    )


async def list_assessments(ctx: OperationContext, case_id: uuid.UUID) -> list[AssessmentSummaryOut]:
    await get_case(ctx, case_id)
    rows = list(
        await ctx.session.scalars(
            select(Assessment)
            .where(Assessment.case_id == case_id)
            .order_by(Assessment.version.desc())
        )
    )
    current: dict[str, str] = {}
    out = []
    for a in rows:
        if a.policy_code not in current:
            current[a.policy_code] = await current_inputs_sha256(ctx, case_id, a.policy_code)
        out.append(await _summary(ctx, a, current[a.policy_code]))
    return out


async def get_assessment(
    ctx: OperationContext, case_id: uuid.UUID, assessment_id: uuid.UUID
) -> AssessmentOut:
    await get_case(ctx, case_id)
    a = await ctx.session.scalar(
        select(Assessment).where(Assessment.case_id == case_id, Assessment.id == assessment_id)
    )
    if a is None:
        raise NotFoundError("Assessment not found")
    if a.result is not None and profile_service.sha256_of(a.result) != a.result_sha256:
        raise ConflictError("The assessment's stored result doesn't match its hash")
    steps = list(
        await ctx.session.scalars(
            select(AssessmentStep)
            .where(AssessmentStep.assessment_id == a.id)
            .order_by(AssessmentStep.seq)
        )
    )
    decisions = await _decisions(ctx, a.id)
    names = await profile_service._members(
        ctx, {d.decided_by_user_id for d in decisions if d.decided_by_user_id}
    )
    summary = await _summary(ctx, a, await current_inputs_sha256(ctx, case_id, a.policy_code))
    return AssessmentOut(
        **summary.model_dump(),
        summary=a.summary,
        result=AssessmentResult.model_validate(json.loads(a.result)) if a.result else None,
        result_sha256=a.result_sha256,
        steps=[StepOut.model_validate(s, from_attributes=True) for s in steps],
        decisions=[
            DecisionOut(
                id=d.id,
                decision=d.decision,
                override_outcome=d.override_outcome,
                reason=d.reason,
                decided_by=names.get(d.decided_by_user_id) if d.decided_by_user_id else None,
                created_at=d.created_at,
            )
            for d in decisions
        ],
    )


async def decide(
    ctx: OperationContext, case_id: uuid.UUID, assessment_id: uuid.UUID, body: DecisionIn
) -> DecisionOut:
    """The broker's decision. The agent never decides: accepting, overriding (with a reason),
    requesting information and escalating are broker acts, each audited."""
    await get_case(ctx, case_id, for_update=True)
    a = await ctx.session.scalar(
        select(Assessment).where(Assessment.case_id == case_id, Assessment.id == assessment_id)
    )
    if a is None:
        raise NotFoundError("Assessment not found")
    if a.status != AssessmentStatus.COMPLETED.value:
        raise AssessmentNotFinishedError()
    if body.decision == "override":
        if body.override_outcome is None or not body.reason:
            raise BusinessRuleError("An override needs the outcome and a documented reason")
        if body.override_outcome == a.outcome:
            raise BusinessRuleError("The override is the same as the assessed outcome")
    elif body.override_outcome is not None:
        raise BusinessRuleError("override_outcome is only for an override")
    if body.decision in {"request_information", "escalate"} and not body.reason:
        raise BusinessRuleError("Give a reason")
    if body.decision == "request_information" and not body.request_title:
        raise BusinessRuleError("Say what information is requested (request_title)")

    decision = AssessmentDecision(
        tenant_id=ctx.tenant_id,
        case_id=case_id,
        assessment_id=a.id,
        decision=body.decision,
        override_outcome=body.override_outcome,
        reason=body.reason,
        decided_by_user_id=ctx.actor.user_id,
    )
    ctx.session.add(decision)
    if body.decision == "request_information":
        ctx.session.add(
            EvidenceRequest(
                tenant_id=ctx.tenant_id,
                case_id=case_id,
                assessment_id=a.id,
                gap_code=f"broker:{uuid.uuid4()}",
                category="information",
                title=body.request_title or "",
                reason=body.reason or "",
                document_type=body.request_document_type,
                supported_on_platform=False,
                requested_by="broker",
                requested_by_user_id=ctx.actor.user_id,
                status=RequestStatus.OPEN.value,
            )
        )
    await ctx.session.flush()
    change = audit.ChangeSet()
    for name, value in (
        ("assessment_id", a.id),
        ("decision", body.decision),
        ("outcome", a.outcome),
        ("override_outcome", body.override_outcome),
        ("reason", body.reason),
    ):
        change.note(name, value)
    await audit.record(
        ctx.session,
        tenant_id=ctx.tenant_id,
        actor=ctx.actor,
        action=AuditAction.ASSESSMENT_DECIDED,
        entity_type="assessment_decision",
        entity_id=decision.id,
        case_id=case_id,
        change_set=change,
    )
    names = await profile_service._members(ctx, {ctx.actor.user_id} if ctx.actor.user_id else set())
    return DecisionOut(
        id=decision.id,
        decision=decision.decision,
        override_outcome=decision.override_outcome,
        reason=decision.reason,
        decided_by=names.get(ctx.actor.user_id) if ctx.actor.user_id else None,
        created_at=decision.created_at,
    )


async def list_requests(ctx: OperationContext, case_id: uuid.UUID) -> list[EvidenceRequestOut]:
    await get_case(ctx, case_id)
    rows = await ctx.session.scalars(
        select(EvidenceRequest)
        .where(EvidenceRequest.case_id == case_id)
        .order_by(EvidenceRequest.status, EvidenceRequest.created_at.desc())
    )
    return [EvidenceRequestOut.model_validate(r, from_attributes=True) for r in rows]


async def withdraw_request(
    ctx: OperationContext, case_id: uuid.UUID, request_id: uuid.UUID
) -> EvidenceRequestOut:
    ctx.require(Permission.ASSESSMENT_DECIDE)
    await get_case(ctx, case_id, for_update=True)
    r = await ctx.session.scalar(
        select(EvidenceRequest).where(
            EvidenceRequest.case_id == case_id, EvidenceRequest.id == request_id
        )
    )
    if r is None:
        raise NotFoundError("Evidence request not found")
    if r.status != RequestStatus.OPEN.value:
        raise ConflictError("Only an open request can be withdrawn")
    r.status = RequestStatus.WITHDRAWN.value
    r.closed_at = utcnow()
    await ctx.session.flush()
    change = audit.ChangeSet()
    change.note("gap_code", r.gap_code)
    change.note("status", r.status)
    await audit.record(
        ctx.session,
        tenant_id=ctx.tenant_id,
        actor=ctx.actor,
        action=AuditAction.EVIDENCE_REQUEST_CLOSED,
        entity_type="evidence_request",
        entity_id=r.id,
        case_id=case_id,
        change_set=change,
    )
    return EvidenceRequestOut.model_validate(r, from_attributes=True)


async def scenarios(
    ctx: OperationContext, case_id: uuid.UUID, body: ScenariosIn, today: date | None = None
) -> ScenariosOut:
    """The broker's scenario calculator: the same deterministic engine, read-only."""
    policy = get_policy(policy_for(await get_case(ctx, case_id), body.policy_code))
    inputs, _ = await load_inputs(ctx, case_id, policy, today or date.today())  # noqa: DTZ011
    if not inputs.counted_income:
        return ScenariosOut(base=None, scenarios=[])
    base = engine.assess(policy, base_inputs(inputs, policy))
    return ScenariosOut(
        base=affordability_out(base),
        scenarios=scenarios_for(policy, inputs, base, body.scenarios),
    )
