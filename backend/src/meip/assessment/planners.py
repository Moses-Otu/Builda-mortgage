"""Planners decide the agent's next action. Two implementations, one interface:

* :class:`OpenAIPlanner` — the model reasons over the tool results and chooses the next tool
  through function calling (OpenAI Responses API, ``store: false``). It sees applicant labels,
  never names. It can only call registered tools; it never computes figures (decision P5).
* :class:`RulesPlanner` — a deterministic planner that chooses the next tool from what the
  previous tools returned. Used in local and test, and as the fallback when no model is
  configured or the model is unavailable (the same pattern as the synthetic and OpenAI
  extraction providers). Its explanation is assembled from tool outputs only.

Both follow a reasoning-action-observation cycle: ``next()`` proposes one tool call;
``observe()`` receives its result before the next proposal.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from meip.assessment.tools import TOOLS, json_schema
from meip.core.config import Settings

RETRY_STATUS = frozenset({408, 409, 429, 500, 502, 503, 504})
MAX_OUTPUT_CHARS = 24_000
PROMPT_VERSION = "assessment-agent/1"


class PlannerUnavailableError(Exception):
    """The model can't be reached or refused: the agent falls back to the rules planner."""


@dataclass
class PlannedCall:
    tool: str
    args: dict[str, Any]
    rationale: str | None = None
    call_id: str | None = None


class Planner(Protocol):
    name: str
    model: str | None

    async def next(self) -> PlannedCall: ...

    def observe(
        self, call: PlannedCall, output: dict[str, Any] | None, error: str | None
    ) -> None: ...


# --- rules planner --------------------------------------------------------------------------------


def plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def _gbp(minor: int | None) -> str:
    return "unknown" if minor is None else f"£{minor / 100:,.0f}"


@dataclass
class RulesPlanner:
    """Chooses the next tool from the facts gathered so far. Different circumstances lead to
    different actions: discrepancies are investigated and escalated, missing loan details skip
    affordability, a shortfall triggers scenarios, gaps become evidence requests."""

    name: str = "rules"
    model: str | None = None
    done: dict[str, dict[str, Any]] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    findings_recorded: set[str] = field(default_factory=set)
    requested: bool = False
    escalated: bool = False
    staged: bool = False

    def observe(self, call: PlannedCall, output: dict[str, Any] | None, error: str | None) -> None:
        if output is not None:
            self.done[call.tool] = output
        if error is not None:
            self.errors[call.tool] = error
            self.done.setdefault(call.tool, {})

    def _open_discrepancies(self) -> list[dict[str, Any]]:
        return [
            d
            for d in self.done.get("reconcile_financial_evidence", {}).get("discrepancies", [])
            if not d.get("acknowledged")
        ]

    def _requestable(self) -> list[dict[str, Any]]:
        return [
            g
            for g in self.done.get("identify_evidence_gaps", {}).get("gaps", [])
            if g["category"] in {"document", "information"}
        ]

    async def next(self) -> PlannedCall:
        d = self.done
        for tool, why in (
            (
                "get_assessment_context",
                "Establish the case, the applicants and what changed since the last assessment.",
            ),
            (
                "get_applicant_profile",
                "Read each applicant's income, commitments and living costs.",
            ),
            (
                "get_financial_evidence",
                "See which documents support which income, and where salary receipt is shown.",
            ),
            ("reconcile_financial_evidence", "Check the documents agree with each other."),
        ):
            if tool not in d:
                return PlannedCall(tool, {}, why)
        for disc in d.get("reconcile_financial_evidence", {}).get("discrepancies", []):
            if disc["key"] not in self.findings_recorded:
                self.findings_recorded.add(disc["key"])
                acknowledged = bool(disc.get("acknowledged"))
                parts = [disc["message"], *disc.get("analysis", [])]
                if acknowledged and disc.get("acknowledgement_note"):
                    parts.append(f"Broker's note: {disc['acknowledgement_note']}")
                return PlannedCall(
                    "record_finding",
                    {
                        "title": (
                            f"{'Reviewed discrepancy' if acknowledged else 'Discrepancy'}: "
                            f"{disc['title']}"
                        )[:200],
                        "detail": " ".join(parts)[:1000],
                        "severity": "info" if acknowledged else "warning",
                        "refs": [disc["key"]],
                    },
                    "Record what the evidence comparison shows for this discrepancy."
                    if not acknowledged
                    else "Note this discrepancy and how the broker explained it.",
                )
        for tool, why in (
            (
                "validate_income",
                "Work out which income counts, and how well each source is evidenced.",
            ),
            ("calculate_monthly_commitments", "Total the existing commitments and living costs."),
            ("calculate_disposable_income", "Find the income left before any mortgage payment."),
            ("identify_evidence_gaps", "List what evidence or information is still missing."),
        ):
            if tool not in d:
                return PlannedCall(tool, {}, why)
        case = d["get_assessment_context"].get("case", {})
        counted = d["validate_income"].get("qualifying_gross_monthly_minor", 0) > 0
        has_loan = bool(case.get("loan_amount_minor") and case.get("property_value_minor"))
        if counted and has_loan and "calculate_affordability" not in d:
            return PlannedCall(
                "calculate_affordability", {}, "Calculate affordability for the requested loan."
            )
        if "evaluate_eligibility" not in d:
            return PlannedCall(
                "evaluate_eligibility", {}, "Evaluate the pre-qualification criteria."
            )
        afford = d.get("calculate_affordability", {})
        if (
            afford
            and afford.get("requested_within_limits") is False
            and "simulate_mortgage_scenarios" not in d
        ):
            return PlannedCall(
                "simulate_mortgage_scenarios",
                {},
                "The requested loan exceeds a limit: compare what would have to change.",
            )
        requestable = self._requestable()
        if requestable and not self.requested:
            self.requested = True
            return PlannedCall(
                "generate_evidence_request",
                {"gap_codes": [g["code"] for g in requestable]},
                "Ask for the missing evidence and information, with the reason for each.",
            )
        outcome = d["evaluate_eligibility"].get("outcome")
        open_disc = self._open_discrepancies()
        if not self.escalated and (open_disc or outcome == "not_currently_eligible"):
            self.escalated = True
            reasons = []
            if open_disc:
                reasons.append(
                    f"{plural(len(open_disc), 'discrepancy', 'discrepancies')} need the "
                    f"broker's review."
                )
            if outcome == "not_currently_eligible":
                reasons.append(
                    "A pre-qualification criterion is not met: "
                    + "; ".join(d["evaluate_eligibility"].get("unmet", [])[:3])
                )
            return PlannedCall(
                "escalate_to_broker",
                {"reason": " ".join(reasons)[:1000], "refs": [x["key"] for x in open_disc][:20]},
                "Only the broker can resolve these.",
            )
        if not self.staged:
            self.staged = True
            stage = (
                "awaiting_broker"
                if self.escalated
                else "awaiting_evidence"
                if self.requested
                else "ready_for_review"
            )
            return PlannedCall(
                "update_assessment_status", {"stage": stage}, "Record where the assessment stands."
            )
        return PlannedCall(
            "generate_assessment_report", self.report(), "Compile the evidence-backed assessment."
        )

    def report(self) -> dict[str, Any]:
        d = self.done
        e = d.get("evaluate_eligibility", {})
        a = d.get("calculate_affordability", {})
        income = d.get("validate_income", {})
        gaps = d.get("identify_evidence_gaps", {}).get("gaps", [])
        open_disc = self._open_discrepancies()
        lines = [f"Indicative outcome: {e.get('outcome_label', 'not evaluated')}."]
        if income:
            lines.append(
                f"Qualifying income {_gbp(income.get('qualifying_gross_monthly_minor'))} "
                f"gross a month "
                f"({_gbp((income.get('qualifying_gross_monthly_minor') or 0) * 12)} a year)."
            )
            unverified = [s for s in income.get("sources", []) if s["verification"] != "verified"]
            if unverified:
                lines.append(
                    f"{plural(len(unverified), 'income source')} not verified by a salary credit: "
                    + ", ".join(
                        f"{s['applicant']} {s['kind']} ({s['verification']})" for s in unverified
                    )
                    + "."
                )
        if a:
            lines.append(
                f"Requested {_gbp(a.get('loan_amount_minor'))}; indicated maximum "
                f"{_gbp(a.get('max_mortgage_minor'))}, limited by "
                f"{(a.get('binding_constraint') or 'n/a').replace('_', ' ')}."
            )
            if a.get("stressed_monthly_repayment_minor") is not None:
                lines.append(
                    f"Repayment {_gbp(a.get('monthly_repayment_minor'))} a month "
                    f"({_gbp(a.get('stressed_monthly_repayment_minor'))} stressed at "
                    f"{a.get('stressed_rate_percent')} %); DTI {a.get('dti_percent')} %, "
                    f"LTV {a.get('ltv_percent')} %, LTI {a.get('lti')}x."
                )
        if open_disc:
            lines.append(
                f"{plural(len(open_disc), 'discrepancy', 'discrepancies')} await the "
                f"broker's review."
            )
        if gaps:
            lines.append(f"{plural(len(gaps), 'evidence or information gap')} open.")
        recs: list[str] = []
        for g in gaps[:5]:
            recs.append(f"{g['title']}: {g['reason']}"[:300])
        for disc in open_disc[:3]:
            recs.append(
                f"Review and acknowledge the discrepancy '{disc['title']}' once explained."[:300]
            )
        if a and a.get("requested_within_limits") is False:
            recs.append(
                f"Consider a loan of up to {_gbp(a.get('max_mortgage_minor'))}, a larger "
                f"deposit or "
                "a longer term (see the scenarios)."
            )
        return {"summary": " ".join(lines)[:3000], "recommendations": recs[:8]}


# --- OpenAI planner -------------------------------------------------------------------------------

INSTRUCTIONS = """\
You are the Mortgage Assessment Agent for a UK mortgage broker firm. You assess one case at a
time by calling tools, then finish by calling generate_assessment_report.

How to work:
- Start with get_assessment_context. Decide each next step from what the tools returned, not
  from a fixed list. Investigate before concluding.
- When documents disagree (discrepancies), investigate them with the evidence tools and
  record_finding what the comparison shows. If you can't explain a difference from the
  evidence, escalate_to_broker.
- Use identify_evidence_gaps, then generate_evidence_request for document and information gaps
  that matter. Never request evidence for a gap that wasn't identified.
- When affordability shows the requested loan exceeds a limit, use simulate_mortgage_scenarios
  to compare alternatives. Scenarios are hypothetical and never change the base assessment.
- evaluate_eligibility gives the outcome. You must not change it, argue around it, or adjust
  inputs to improve it.

Rules you must never break:
- Never calculate figures yourself. Quote numbers only from tool outputs (money is in pence:
  divide by 100 for pounds).
- Never fabricate missing information or treat declared or extracted figures as verified.
  State limitations plainly.
- Never conceal conflicting evidence.
- You don't make lending decisions or give advice: your outcome is indicative and the broker
  decides.
- Refer to applicants only by their labels (Applicant 1, Applicant 2).
- Every tool call includes "why": one short, plain sentence for the broker.
- Finish with generate_assessment_report: a clear summary of what you investigated, found and
  concluded, and specific recommendations."""


class OpenAIPlanner:
    name = "openai"

    def __init__(
        self,
        settings: Settings,
        client: httpx.AsyncClient | None = None,
        backoff: float = 1.0,
    ) -> None:
        if settings.openai_api_key is None:
            raise ValueError("MEIP_OPENAI_API_KEY is required for the openai planner")
        self._key = settings.openai_api_key.get_secret_value()
        self.model: str | None = settings.agent_model or settings.openai_model
        self._url = settings.openai_base_url.rstrip("/") + "/responses"
        self._timeout = settings.openai_timeout_seconds
        self._attempts = settings.openai_max_attempts
        self._client = client
        self._backoff = backoff
        self._items: list[dict[str, Any]] = [
            {"role": "system", "content": [{"type": "input_text", "text": INSTRUCTIONS}]},
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": "Assess this case. Begin by establishing its context.",
                    }
                ],
            },
        ]
        self._pending: list[PlannedCall] = []
        self._tools = [
            {
                "type": "function",
                "name": t.name,
                "description": t.description,
                "parameters": json_schema(t),
                "strict": False,
            }
            for t in TOOLS.values()
        ]

    def observe(self, call: PlannedCall, output: dict[str, Any] | None, error: str | None) -> None:
        payload = output if output is not None else {"error": error}
        text = json.dumps(payload, ensure_ascii=False, default=str)
        if len(text) > MAX_OUTPUT_CHARS:
            text = text[:MAX_OUTPUT_CHARS] + '…"(truncated)"'
        self._items.append(
            {"type": "function_call_output", "call_id": call.call_id, "output": text}
        )

    async def next(self) -> PlannedCall:
        if self._pending:
            return self._pending.pop(0)
        for nudge in (False, True):
            body = await self._respond()
            calls = []
            for item in body.get("output") or []:
                kind = item.get("type")
                if kind == "function_call":
                    self._items.append(
                        {
                            "type": "function_call",
                            "call_id": item.get("call_id"),
                            "name": item.get("name"),
                            "arguments": item.get("arguments", "{}"),
                        }
                    )
                    calls.append(item)
                elif kind == "message":
                    for content in item.get("content") or []:
                        if content.get("type") == "refusal":
                            raise PlannerUnavailableError("model_refused")
                        if content.get("type") == "output_text":
                            self._items.append(
                                {
                                    "role": "assistant",
                                    "content": [
                                        {"type": "output_text", "text": content.get("text", "")}
                                    ],
                                }
                            )
            if calls:
                for c in calls:
                    try:
                        args = json.loads(c.get("arguments") or "{}")
                    except ValueError:
                        args = {"__unparseable__": c.get("arguments")}
                    if not isinstance(args, dict):
                        args = {"__unparseable__": args}
                    why = args.pop("why", None)
                    self._pending.append(
                        PlannedCall(
                            str(c.get("name")),
                            args,
                            str(why)[:1000] if why else None,
                            c.get("call_id"),
                        )
                    )
                return self._pending.pop(0)
            if not nudge:
                self._items.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_text",
                                "text": "Continue by calling a tool. When you have finished, call "
                                "generate_assessment_report.",
                            }
                        ],
                    }
                )
        raise PlannerUnavailableError("no_tool_call")

    async def _respond(self) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "store": False,
            "temperature": 0,
            "parallel_tool_calls": False,
            "input": self._items,
            "tools": self._tools,
            "tool_choice": "auto",
        }
        headers = {"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"}
        client = self._client or httpx.AsyncClient(timeout=self._timeout)
        delay = self._backoff
        try:
            for attempt in range(1, self._attempts + 1):
                try:
                    response = await client.post(self._url, json=payload, headers=headers)
                except (httpx.TimeoutException, httpx.TransportError):
                    if attempt == self._attempts:
                        raise PlannerUnavailableError("provider_unreachable") from None
                else:
                    if response.status_code == 200:
                        try:
                            body: dict[str, Any] = response.json()
                        except ValueError:
                            raise PlannerUnavailableError("malformed_response") from None
                        if body.get("status") not in (None, "completed"):
                            raise PlannerUnavailableError(f"response_{body.get('status')}")
                        return body
                    if response.status_code not in RETRY_STATUS or attempt == self._attempts:
                        raise PlannerUnavailableError(f"provider_http_{response.status_code}")
                await asyncio.sleep(delay)
                delay *= 2
        finally:
            if self._client is None:
                await client.aclose()
        raise PlannerUnavailableError("provider_unreachable")


def build_planner(settings: Settings, client: httpx.AsyncClient | None = None) -> Planner:
    if settings.agent_planner == "openai" and settings.openai_api_key is not None:
        return OpenAIPlanner(settings, client=client)
    return RulesPlanner()
