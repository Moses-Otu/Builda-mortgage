"""The Mortgage Assessment Agent: a controlled reasoning-action-observation loop.

1. **Reason:** the planner proposes the next tool call (with a short public "why").
2. **Act:** the call is checked (a registered tool, a valid input, under the repeat limit) and
   run in its own transaction as the agent's system actor, whose capabilities allow only
   assessment writes.
3. **Observe:** the result (or the error) is recorded as a step and given back to the planner.

The run ends when the planner calls ``generate_assessment_report``, or at the step limit (the
report is then compiled from the tool results, and the limit is recorded). A repeated call with
the same input returns the recorded output (``cached``). If the model is unavailable the run
continues with the rules planner, and says so. Nothing the agent does changes financial data,
reviews, documents or approvals.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import date
from typing import Any

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from meip.assessment.models import (
    Assessment,
    AssessmentStatus,
    AssessmentStep,
    EvidenceRequest,
    RequestStatus,
    StepKind,
    StepStatus,
)
from meip.assessment.planners import (
    PlannedCall,
    Planner,
    PlannerUnavailableError,
    RulesPlanner,
    build_planner,
)
from meip.assessment.policy import get_policy
from meip.assessment.tools import (
    TOOLS,
    RunState,
    ToolCall,
    ToolError,
    canonical,
    idempotency_key,
    parse_args,
)
from meip.audit import service as audit
from meip.audit.service import AuditAction
from meip.core.config import Settings
from meip.core.db import utcnow
from meip.core.logging import get_logger
from meip.profile.service import sha256_of
from meip.tenancy.context import Actor, OperationContext, open_tenant_scope
from meip.tenancy.permissions import Permission

_log = get_logger("meip.assessment.agent")
REPEAT_LIMIT = 6


def agent_actor(user_id: uuid.UUID | None) -> Actor:
    return Actor.system(
        "agent:assessment",
        capabilities=frozenset({Permission.ASSESSMENT_RUN}),
        on_behalf_of_user_id=user_id,
    )


class _Runner:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        settings: Settings,
        tenant_id: uuid.UUID,
        assessment_id: uuid.UUID,
        user_id: uuid.UUID | None,
        planner: Planner,
        today: date,
    ) -> None:
        self.sessionmaker = sessionmaker
        self.settings = settings
        self.tenant_id = tenant_id
        self.assessment_id = assessment_id
        self.actor = agent_actor(user_id)
        self.planner = planner
        self.today = today
        self.seq = 0
        self.cache: dict[str, dict[str, Any]] = {}
        self.outputs: dict[str, dict[str, Any]] = {}  # tool -> latest successful output
        self.counts: dict[str, int] = {}
        self.state: RunState | None = None

    def ctx(self, scope: Any) -> OperationContext:
        return OperationContext(
            scope=scope, actor=self.actor, correlation_id=str(self.assessment_id)
        )

    async def _record(
        self,
        ctx: OperationContext,
        *,
        kind: StepKind,
        tool: str | None,
        rationale: str | None,
        args: dict[str, Any] | None,
        output: dict[str, Any] | None,
        status: StepStatus,
        error_code: str | None = None,
        key: str | None = None,
        duration_ms: int | None = None,
    ) -> None:
        assert self.state is not None  # noqa: S101
        self.seq += 1
        ctx.session.add(
            AssessmentStep(
                tenant_id=self.tenant_id,
                case_id=self.state.case_id,
                assessment_id=self.assessment_id,
                seq=self.seq,
                kind=kind.value,
                tool=tool,
                rationale=(rationale or None) and rationale[:1000],
                input=args,
                output=output,
                status=status.value,
                error_code=error_code,
                idempotency_key=key,
                duration_ms=duration_ms,
            )
        )
        await ctx.session.execute(
            update(Assessment)
            .where(Assessment.id == self.assessment_id)
            .values(step_count=self.seq)
            .execution_options(synchronize_session=False)
        )

    async def note(self, text: str, *, error_code: str | None = None) -> None:
        async with open_tenant_scope(self.sessionmaker, self.tenant_id) as scope:
            ctx = self.ctx(scope)
            await self._record(
                ctx,
                kind=StepKind.ERROR if error_code else StepKind.NOTE,
                tool=None,
                rationale=text,
                args=None,
                output=None,
                status=StepStatus.ERROR if error_code else StepStatus.OK,
                error_code=error_code,
            )
            await ctx.commit()

    async def start(self) -> bool:
        async with open_tenant_scope(self.sessionmaker, self.tenant_id) as scope:
            ctx = self.ctx(scope)
            a = await ctx.session.scalar(
                select(Assessment).where(Assessment.id == self.assessment_id).with_for_update()
            )
            if a is None or a.status != AssessmentStatus.QUEUED.value:
                return False
            previous: dict[str, Any] | None = None
            if a.previous_assessment_id is not None:
                p = await ctx.session.get(Assessment, a.previous_assessment_id)
                if p is not None:
                    previous = {
                        "id": p.id,
                        "version": p.version,
                        "outcome": p.outcome,
                        "finished_at": p.finished_at.isoformat() if p.finished_at else None,
                        "profile_fingerprint": p.profile_fingerprint,
                        "result": json.loads(p.result) if p.result else None,
                    }
            self.state = RunState(
                assessment_id=a.id,
                case_id=a.case_id,
                version=a.version,
                policy=get_policy(a.policy_code),
                today=self.today,
                previous=previous,
            )
            a.status = AssessmentStatus.RUNNING.value
            a.started_at = utcnow()
            a.planner = self.planner.name
            a.model = self.planner.model
            await ctx.commit()
        return True

    async def step(self, planned: PlannedCall) -> tuple[dict[str, Any] | None, str | None]:
        assert self.state is not None  # noqa: S101
        tool = TOOLS.get(planned.tool)
        args = {k: v for k, v in planned.args.items() if k != "why"}
        key = idempotency_key(planned.tool, args)
        async with open_tenant_scope(self.sessionmaker, self.tenant_id) as scope:
            ctx = self.ctx(scope)
            started = time.perf_counter()
            if tool is None:
                message = f"There is no tool named {planned.tool!r}."
                await self._record(
                    ctx,
                    kind=StepKind.TOOL,
                    tool=planned.tool[:48],
                    rationale=planned.rationale,
                    args=args,
                    output={"error": message},
                    status=StepStatus.ERROR,
                    error_code="unknown_tool",
                    key=key,
                )
                await ctx.commit()
                return None, message
            if key in self.cache:
                output = self.cache[key]
                await self._record(
                    ctx,
                    kind=StepKind.TOOL,
                    tool=tool.name,
                    rationale=planned.rationale,
                    args=args,
                    output=output,
                    status=StepStatus.CACHED,
                    key=key,
                    duration_ms=0,
                )
                await ctx.commit()
                return output, None
            self.counts[tool.name] = self.counts.get(tool.name, 0) + 1
            try:
                if self.counts[tool.name] > REPEAT_LIMIT:
                    raise ToolError(
                        "repeat_limit", f"{tool.name} has been called too often in this assessment."
                    )
                parsed = parse_args(tool, args)
                output = await tool.handler(ToolCall(ctx=ctx, state=self.state), parsed)
                output = _jsonable(output)
            except ToolError as exc:
                await ctx.session.rollback()
                return await self._failed(
                    planned, tool.name, args, key, exc.code, exc.message, started
                )
            except Exception as exc:
                await ctx.session.rollback()
                _log.warning("tool_failed", tool=tool.name, error_type=type(exc).__name__)
                return await self._failed(
                    planned,
                    tool.name,
                    args,
                    key,
                    "tool_failed",
                    f"{tool.name} failed unexpectedly ({type(exc).__name__}). Continue "
                    f"without it or escalate.",
                    started,
                )
            self.cache[key] = output
            self.outputs[tool.name] = output
            await self._record(
                ctx,
                kind=StepKind.TOOL,
                tool=tool.name,
                rationale=planned.rationale,
                args=args,
                output=output,
                status=StepStatus.OK,
                key=key,
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
            await ctx.commit()
            return output, None

    async def _failed(
        self,
        planned: PlannedCall,
        tool: str,
        args: dict[str, Any],
        key: str,
        code: str,
        message: str,
        started: float,
    ) -> tuple[None, str]:
        async with open_tenant_scope(self.sessionmaker, self.tenant_id) as scope:
            ctx = self.ctx(scope)
            await self._record(
                ctx,
                kind=StepKind.TOOL,
                tool=tool,
                rationale=planned.rationale,
                args=args,
                output={"error": message},
                status=StepStatus.ERROR,
                error_code=code,
                key=key,
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
            await ctx.commit()
        return None, message

    async def finish(self) -> None:
        assert self.state is not None  # noqa: S101 - set by start()
        assert self.state.finished is not None  # noqa: S101 - set by the report tool
        result = self.state.finished
        text = canonical(result.model_dump(mode="json"))
        async with open_tenant_scope(self.sessionmaker, self.tenant_id) as scope:
            ctx = self.ctx(scope)
            a = await ctx.session.scalar(
                select(Assessment).where(Assessment.id == self.assessment_id).with_for_update()
            )
            assert a is not None  # noqa: S101
            # Evidence requests whose gap no longer exists are fulfilled.
            current = {g.code for g in (self.state.gaps or [])}
            fulfilled = 0
            for r in await ctx.session.scalars(
                select(EvidenceRequest).where(
                    EvidenceRequest.case_id == self.state.case_id,
                    EvidenceRequest.status == RequestStatus.OPEN.value,
                    EvidenceRequest.requested_by == "agent",
                )
            ):
                if r.gap_code not in current:
                    r.status = RequestStatus.FULFILLED.value
                    r.closed_at = utcnow()
                    r.closed_by_assessment_id = a.id
                    fulfilled += 1
            a.status = AssessmentStatus.COMPLETED.value
            a.outcome = result.eligibility.outcome
            a.summary = result.summary
            a.result = text
            a.result_sha256 = sha256_of(text)
            a.profile_fingerprint = result.profile_fingerprint
            a.finished_at = utcnow()
            a.step_count = self.seq
            change = audit.ChangeSet()
            for name, value in (
                ("assessment_id", a.id),
                ("assessment_version", a.version),
                ("outcome", a.outcome),
                ("policy_code", a.policy_code),
                ("planner", a.planner),
                ("step_count", self.seq),
                ("result_sha256", a.result_sha256),
                ("fulfilled_count", fulfilled),
            ):
                change.note(name, value)
            await audit.record(
                ctx.session,
                tenant_id=self.tenant_id,
                actor=self.actor,
                action=AuditAction.ASSESSMENT_COMPLETED,
                entity_type="assessment",
                entity_id=a.id,
                case_id=self.state.case_id,
                change_set=change,
            )
            await ctx.commit()

    async def fail(self, code: str) -> None:
        async with open_tenant_scope(self.sessionmaker, self.tenant_id) as scope:
            ctx = self.ctx(scope)
            a = await ctx.session.scalar(
                select(Assessment).where(Assessment.id == self.assessment_id).with_for_update()
            )
            if a is None or a.status in {
                AssessmentStatus.COMPLETED.value,
                AssessmentStatus.FAILED.value,
            }:
                return
            a.status = AssessmentStatus.FAILED.value
            a.error_code = code
            a.finished_at = utcnow()
            change = audit.ChangeSet()
            change.note("assessment_id", a.id)
            change.note("error_code", code)
            await audit.record(
                ctx.session,
                tenant_id=self.tenant_id,
                actor=self.actor,
                action=AuditAction.ASSESSMENT_FAILED,
                entity_type="assessment",
                entity_id=a.id,
                case_id=a.case_id,
                change_set=change,
            )
            await ctx.commit()


def _jsonable(value: Any) -> Any:
    return json.loads(canonical(value))


async def run(
    sessionmaker: async_sessionmaker[AsyncSession],
    settings: Settings,
    tenant_id: uuid.UUID,
    assessment_id: uuid.UUID,
    user_id: uuid.UUID | None,
    *,
    planner: Planner | None = None,
    client: httpx.AsyncClient | None = None,
    today: date | None = None,
) -> None:
    """Run one queued assessment to completion (or failure). Safe to call twice: only a
    queued assessment starts."""
    runner = _Runner(
        sessionmaker,
        settings,
        tenant_id,
        assessment_id,
        user_id,
        planner or build_planner(settings, client),
        today or date.today(),  # noqa: DTZ011 - the assessment date is a calendar date
    )
    try:
        if not await runner.start():
            return
        assert runner.state is not None  # noqa: S101
        fallback_used = False
        for _ in range(settings.agent_max_steps):
            try:
                planned = await runner.planner.next()
            except PlannerUnavailableError as exc:
                if isinstance(runner.planner, RulesPlanner) or fallback_used:
                    raise
                fallback_used = True
                await runner.note(
                    f"The model is unavailable ({exc}); continuing with the deterministic rules "
                    "planner. Earlier tool results are reused.",
                    error_code="planner_fallback",
                )
                # The rules planner picks up from what's known; repeats return recorded outputs.
                runner.planner = RulesPlanner(done=dict(runner.outputs))
                continue
            output, error = await runner.step(planned)
            runner.planner.observe(planned, output, error)
            if planned.tool == "generate_assessment_report" and output is not None:
                break
        if runner.state.finished is None:
            await runner.note(
                "The step limit was reached before the agent finished; the report was compiled "
                "from the tool results gathered so far.",
                error_code="step_limit",
            )
            report = RulesPlanner(done=dict(runner.outputs)).report()
            output, error = await runner.step(
                PlannedCall("generate_assessment_report", report, "Compile the assessment.")
            )
            if output is None:
                raise RuntimeError(error or "report_failed")
        await runner.finish()
    except Exception as exc:
        _log.error(
            "assessment_failed", assessment_id=str(assessment_id), error_type=type(exc).__name__
        )
        code = str(exc) if isinstance(exc, PlannerUnavailableError) else "agent_error"
        await runner.fail(code[:48])


__all__ = ["agent_actor", "run"]
