"""The agentic pre-qualification and affordability assessment."""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, BackgroundTasks, Request, Response, status

from meip.api.deps import (
    AssessmentDecideCtx,
    AssessmentRunCtx,
    CaseReadCtx,
    DocumentReadCtx,
)
from meip.assessment import agent, journey, service
from meip.assessment.schemas import (
    AssessmentOut,
    AssessmentSummaryOut,
    DecisionIn,
    DecisionOut,
    EvidenceRequestOut,
    JourneyOut,
    PolicyOut,
    PositionOut,
    ScenariosIn,
    ScenariosOut,
    StartedOut,
    StartIn,
)

router = APIRouter(prefix="/api/v1/cases/{case_id}", tags=["assessment"])
reference = APIRouter(prefix="/api/v1/assessment", tags=["assessment"])

INDICATIVE = (
    " Indicative only: not a lending decision or advice; the broker makes the final decision."
)


@reference.get(
    "/policies",
    response_model=list[PolicyOut],
    summary="Lending policies the assessment can use",
    description="Each policy's version code and every parameter its figures are computed from.",
)
async def policies(ctx: CaseReadCtx) -> list[PolicyOut]:
    return service.list_policies()


@router.get(
    "/journey",
    response_model=JourneyOut,
    summary="Where the case is in the guided journey",
    description=(
        "The six stages (applicant and loan details, income evidence, bank statements, profile "
        "approval, mortgage assessment, broker decision), each with its outstanding "
        "requirements; the current stage; and the single next action. Derived from the "
        "case's state on every read. Evidence requirements adapt to the applicant's income "
        "and the lending policy."
    ),
)
async def get_journey(case_id: uuid.UUID, ctx: DocumentReadCtx) -> JourneyOut:
    return await journey.journey(ctx, case_id, date.today())  # noqa: DTZ011


@router.get(
    "/financial-position",
    response_model=PositionOut,
    summary="The consolidated financial position the assessment will use",
    description=(
        "Each income source with its verification status (verified, extracted, declared, "
        "missing), what counts under the case's policy, commitments, living costs and the "
        "deposit. The assessment's own rules, read-only, for the profile review."
    ),
)
async def get_position(case_id: uuid.UUID, ctx: DocumentReadCtx) -> PositionOut:
    return await service.position(ctx, case_id, date.today())  # noqa: DTZ011


@router.post(
    "/assessments",
    response_model=StartedOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start the assessment agent",
    description=(
        "Queues an assessment and runs the agent in the background: it establishes the case "
        "context, inspects the profile and evidence, reconciles discrepancies, identifies and "
        "requests missing evidence, runs the deterministic affordability and pre-qualification "
        "rules, explores scenarios, escalates to the broker and writes an evidence-backed report. "
        "Poll `GET …/assessments/{id}` for its steps. If nothing changed since the latest "
        "completed assessment, that one is returned (`reused`) unless `force` is set. "
        "Needs `assessment:run`." + INDICATIVE
    ),
    responses={
        409: {
            "description": "`assessment-in-progress`, or `assessment-blocked`: the profile "
            "isn't approved or the loan details are missing"
        }
    },
)
async def start(
    case_id: uuid.UUID,
    body: StartIn,
    ctx: AssessmentRunCtx,
    request: Request,
    background: BackgroundTasks,
    response: Response,
) -> StartedOut:
    out, run = await service.start(ctx, case_id, body)
    await ctx.commit()
    if run:
        background.add_task(
            agent.run,
            request.app.state.sessionmaker,
            request.app.state.settings,
            ctx.tenant_id,
            out.assessment.id,
            ctx.actor.user_id,
        )
    else:
        response.status_code = status.HTTP_200_OK
    return out


@router.get(
    "/assessments",
    response_model=list[AssessmentSummaryOut],
    summary="List the case's assessments",
    description="Newest first. `stale` means the profile or case inputs changed since it ran.",
)
async def list_assessments(case_id: uuid.UUID, ctx: DocumentReadCtx) -> list[AssessmentSummaryOut]:
    return await service.list_assessments(ctx, case_id)


@router.get(
    "/assessments/{assessment_id}",
    response_model=AssessmentOut,
    summary="Get an assessment with its steps and result",
    description=(
        "The agent's steps (each tool call with its input, output and the short reason it gave), "
        "the result (affordability, pre-qualification, scenarios, evidence gaps, discrepancies, "
        "findings, escalations, what changed since the previous assessment) and the broker's "
        "decisions." + INDICATIVE
    ),
    responses={404: {"description": "Case or assessment not found"}},
)
async def get_assessment(
    case_id: uuid.UUID, assessment_id: uuid.UUID, ctx: DocumentReadCtx
) -> AssessmentOut:
    return await service.get_assessment(ctx, case_id, assessment_id)


@router.post(
    "/assessments/{assessment_id}/decisions",
    response_model=DecisionOut,
    status_code=status.HTTP_201_CREATED,
    summary="Record the broker's decision on an assessment",
    description=(
        "`accept`, `override` (with `override_outcome` and a documented `reason`), "
        "`request_information` (opens an evidence request) or `escalate`. Audited. Needs "
        "`assessment:decide` (firm admin, adviser)."
    ),
    responses={409: {"description": "`assessment-not-finished`"}},
)
async def decide(
    case_id: uuid.UUID, assessment_id: uuid.UUID, body: DecisionIn, ctx: AssessmentDecideCtx
) -> DecisionOut:
    out = await service.decide(ctx, case_id, assessment_id, body)
    await ctx.commit()
    return out


@router.get(
    "/evidence-requests",
    response_model=list[EvidenceRequestOut],
    summary="List evidence requests",
    description="Open requests first. Raised by the agent for identified gaps, or by the broker.",
)
async def list_requests(case_id: uuid.UUID, ctx: DocumentReadCtx) -> list[EvidenceRequestOut]:
    return await service.list_requests(ctx, case_id)


@router.post(
    "/evidence-requests/{request_id}/withdrawal",
    response_model=EvidenceRequestOut,
    summary="Withdraw an open evidence request",
)
async def withdraw_request(
    case_id: uuid.UUID, request_id: uuid.UUID, ctx: AssessmentDecideCtx
) -> EvidenceRequestOut:
    out = await service.withdraw_request(ctx, case_id, request_id)
    await ctx.commit()
    return out


@router.post(
    "/affordability/scenarios",
    response_model=ScenariosOut,
    summary="Compare mortgage scenarios",
    description=(
        "The deterministic affordability engine on the case's current figures, with each "
        "scenario changing only the assumptions given. Read-only." + INDICATIVE
    ),
)
async def scenarios(case_id: uuid.UUID, body: ScenariosIn, ctx: DocumentReadCtx) -> ScenariosOut:
    return await service.scenarios(ctx, case_id, body)
