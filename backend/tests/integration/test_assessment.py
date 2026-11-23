"""The agentic assessment end to end, over real (SYNTHETIC) documents, the worker, the API and
PostgreSQL: the brief's acceptance scenarios (§10).

Payslips and statements are generated for the last three complete months, so the evidence
rules see a current applicant whatever day the suite runs."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path
from typing import Any

import asyncpg
import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from meip.assessment import agent
from meip.assessment.planners import OpenAIPlanner, PlannedCall
from meip.core.config import AuthMode, Settings
from meip.core.db import create_engine, create_sessionmaker
from meip.documents.processing import run_once
from meip.documents.storage import FilesystemBlobStore
from meip.profile.service import sha256_of
from tests.conftest import TEST_DB, Firm, MakeFirm, db_url
from tests.synthetic_docs import bank_statement_series, payslip_series, salaries_from

Worker = tuple[async_sessionmaker[AsyncSession], FilesystemBlobStore, Settings]
SECTIONS = ("summary", "payments", "deductions", "year_to_date")


def _start_month(months_back: int) -> tuple[int, int]:
    today = date.today()  # noqa: DTZ011
    year, month = today.year, today.month - months_back
    while month <= 0:
        year, month = year - 1, month + 12
    return year, month


START = _start_month(3)  # the last three complete months


@pytest.fixture
async def worker(settings: Settings, blob_root: Path) -> AsyncIterator[Worker]:
    engine = create_engine(settings.database_url, pool_size=4)
    try:
        yield create_sessionmaker(engine), FilesystemBlobStore(blob_root), settings
    finally:
        await engine.dispose()


@pytest.fixture
async def owner() -> AsyncIterator[asyncpg.Connection]:
    conn = await asyncpg.connect(db_url("meip_owner", TEST_DB, driver="postgresql"))
    try:
        await conn.execute(
            "UPDATE jobs SET status = 'done', locked_until = NULL "
            "WHERE status IN ('queued', 'running')"
        )
        yield conn
    finally:
        await conn.close()


class Case:
    def __init__(
        self, firm: Firm, worker: Worker, case: dict[str, Any], alex: dict[str, Any], source: str
    ):
        self.firm, self.worker, self.case, self.alex, self.source = firm, worker, case, alex, source
        self.id = case["id"]
        self.base = f"/api/v1/cases/{case['id']}"

    async def _upload(self, pdf: bytes, kind: str) -> dict[str, Any]:
        resp = await self.firm.upload(
            self.id, pdf, applicant_id=self.alex["id"], document_type=kind
        )
        assert resp.status_code == 201, resp.text
        while await run_once(*self.worker):
            pass
        doc: dict[str, Any] = (
            await self.firm.request("GET", f"{self.base}/documents/{resp.json()['id']}")
        ).json()
        assert doc["status"] == "extracted", doc
        return doc

    async def _accept(self, doc: dict[str, Any], sections: tuple[str, ...], **body: Any) -> None:
        url = f"{self.base}/documents/{doc['id']}"
        x = (await self.firm.request("GET", f"{url}/extraction")).json()
        resp = await self.firm.request(
            "POST",
            f"{url}/review",
            if_match=doc["version"],
            json={
                "decisions": [
                    {"ordinal": f["ordinal"], "decision": "accept"} for s in sections for f in x[s]
                ],
                **body,
            },
        )
        assert resp.status_code == 201, resp.text

    async def payslips(self, months: int = 3, start: tuple[int, int] = START, **kw: Any) -> None:
        for slip in payslip_series(start, months, **kw):
            doc = await self._upload(slip.document.pdf(), "payslip")
            await self._accept(doc, SECTIONS, income_source_id=self.source, frequency="monthly")

    async def statements(self, months: int = 4, **kw: Any) -> None:
        """Statements from the first payslip month to this month: the salary check looks a few
        days past each month-end pay date, so the latest salary needs this month's statement."""
        salaries = salaries_from(payslip_series(START, 3))
        for s in bank_statement_series(salaries, start=START, months=months, **kw):
            doc = await self._upload(s.document.pdf(), "bank_statement")
            x = (
                await self.firm.request("GET", f"{self.base}/documents/{doc['id']}/extraction")
            ).json()
            match = x["account_match"]
            account = (
                {"bank_account_id": match["account_id"]}
                if match["status"] == "existing"
                else {"new_account": {"holder_ids": [self.alex["id"]], "label": None}}
            )
            await self._accept(doc, ("summary",), **account)

    async def set_loan(self, loan: int, value: int) -> None:
        version = (await self.firm.request("GET", self.base)).json()["version"]
        resp = await self.firm.request(
            "PATCH",
            self.base,
            if_match=version,
            json={
                "loan_amount": {"amount_minor": loan * 100},
                "property_value": {"amount_minor": value * 100},
            },
        )
        assert resp.status_code == 200, resp.text

    async def observe(self, **body: Any) -> None:
        resp = await self.firm.request("POST", f"{self.base}/financial/observations", json=body)
        assert resp.status_code == 201, resp.text

    async def assess(
        self, *, force: bool = False, policy: str = "uk-resi-standard/1", expect: int = 202
    ) -> dict[str, Any]:
        resp = await self.firm.request(
            "POST", f"{self.base}/assessments", json={"force": force, "policy_code": policy}
        )
        assert resp.status_code == expect, resp.text
        started = resp.json()
        got = await self.firm.request(
            "GET", f"{self.base}/assessments/{started['assessment']['id']}"
        )
        assert got.status_code == 200, got.text
        out: dict[str, Any] = got.json()
        out["_reused"] = started["reused"]
        return out

    async def requests(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = (
            await self.firm.request("GET", f"{self.base}/evidence-requests")
        ).json()
        return out


async def _case(firm: Firm, worker: Worker, *, loan: int = 150_000, value: int = 250_000) -> Case:
    c = await firm.create_case(property_use="residential", term_months=300)
    alex = await firm.add_applicant(
        c["id"], first_name="Alex", last_name="Example", date_of_birth="1990-01-01"
    )
    source = await firm.request(
        "POST",
        f"/api/v1/cases/{c['id']}/financial/income-sources",
        json={
            "applicant_id": alex["id"],
            "kind": "employment",
            "basis": "permanent",
            "start_date": "2019-04-01",
            "is_primary": True,
        },
    )
    assert source.status_code == 201, source.text
    case = Case(firm, worker, c, alex, source.json()["id"])
    await case.set_loan(loan, value)
    return case


@pytest.fixture
async def case(firm: Firm, worker: Worker, owner: asyncpg.Connection) -> Case:
    return await _case(firm, worker)


def tools(a: dict[str, Any]) -> list[str]:
    return [s["tool"] for s in a["steps"] if s["kind"] == "tool"]


def criterion(a: dict[str, Any], code: str) -> dict[str, Any]:
    return next(c for c in a["result"]["eligibility"]["criteria"] if c["code"] == code)


# --- 1. complete assessment ---------------------------------------------------------------------


async def test_a_complete_assessment_end_to_end(case: Case, owner: asyncpg.Connection) -> None:
    await case.payslips()
    await case.statements()
    a = await case.assess()
    assert a["status"] == "completed", a
    assert a["planner"] == "rules"
    assert a["version"] == 1
    assert a["trigger"] == "initial"
    result = a["result"]
    assert result["eligibility"]["outcome"] == "potentially_eligible", result["eligibility"][
        "outstanding"
    ]
    assert a["outcome"] == a["final_outcome"] == "potentially_eligible"
    (income,) = result["income"]
    assert income["verification"] == "verified"  # reviewed payslips and exact salary credits
    assert len(income["corroborated_periods"]) == 3
    afford = result["affordability"]
    assert afford["requested_within_limits"] is True
    assert afford["loan_amount_minor"] == 15_000_000
    assert afford["ltv_percent"] == "60.00"
    assert {limit["code"] for limit in afford["limits"]} == {
        "affordability",
        "income_multiple",
        "loan_to_value",
    }
    assert result["notice"].startswith("Indicative")
    # The agent's reasoning-action-observation trail: every step has a reason.
    trail = tools(a)
    assert trail[0] == "get_assessment_context"
    assert trail[-1] == "generate_assessment_report"
    assert "escalate_to_broker" not in trail
    assert "simulate_mortgage_scenarios" not in trail
    assert all(s["rationale"] for s in a["steps"] if s["kind"] == "tool")
    # Applicant names never reach the agent's tool outputs.
    assert "Alex" not in json.dumps([s["output"] for s in a["steps"]])
    # Deposit and living-cost evidence are requested, but don't hold the outcome back.
    codes = {r["gap_code"] for r in await case.requests()}
    assert {"deposit", "expenditure"} <= codes
    # The stored result is hash-checked; the run is audited.
    assert a["result_sha256"] == sha256_of(
        json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    )
    actions = {
        r["action"]
        for r in await owner.fetch(
            "SELECT action FROM audit_events WHERE case_id = $1", uuid.UUID(case.id)
        )
    }
    assert {"assessment.started", "assessment.completed", "evidence.requested"} <= actions


# --- 2, 9, 10. missing documents, new evidence, reassessment --------------------------------------


async def test_missing_statements_are_requested_then_new_evidence_is_reassessed(case: Case) -> None:
    await case.payslips()
    first = await case.assess()
    assert first["result"]["eligibility"]["outcome"] == "eligible_subject_to_verification"
    assert criterion(first, "evidence_complete")["status"] == "unknown"
    assert first["result"]["income"][0]["verification"] == "extracted"  # never assumed verified
    requested = {r["gap_code"]: r for r in await case.requests()}
    statements = next(r for code, r in requested.items() if code.startswith("statements:"))
    assert statements["status"] == "open"
    assert statements["document_type"] == "bank_statement"
    assert len(statements["months"]) == 3
    assert "salary" in statements["reason"]

    await case.statements()
    listed = (await case.firm.request("GET", f"{case.base}/assessments")).json()
    assert listed[0]["stale"] is True  # new evidence since the assessment

    second = await case.assess()
    assert second["version"] == 2
    assert second["trigger"] == "reassessment"
    assert second["previous_assessment_id"] == first["id"]
    assert second["result"]["eligibility"]["outcome"] == "potentially_eligible"
    change = second["result"]["reassessment"]
    assert change["previous_version"] == 1
    kinds = {c["kind"] for c in change["changes"]}
    assert {"verification", "new_evidence"} <= kinds
    assert any("verification" in x for x in change["affected"])
    closed = {r["gap_code"]: r for r in await case.requests()}[statements["gap_code"]]
    assert closed["status"] == "fulfilled"
    assert closed["closed_at"]
    # The first assessment is unchanged: versions are never overwritten.
    again = (await case.firm.request("GET", f"{case.base}/assessments/{first['id']}")).json()
    assert again["result"] == first["result"]
    assert again["result_sha256"] == first["result_sha256"]


# --- 3. conflicting salary ------------------------------------------------------------------------


async def test_a_salary_discrepancy_is_investigated_recorded_and_escalated(case: Case) -> None:
    await case.payslips()
    await case.statements(faults=frozenset({"short_credit"}), fault_at=1)
    a = await case.assess()
    trail = tools(a)
    assert trail.index("record_finding") < trail.index("escalate_to_broker")
    result = a["result"]
    (disc,) = [d for d in result["discrepancies"] if d["rule_code"] == "SALARY-CREDIT"]
    assert not disc["acknowledged"]
    assert any("short by £100.00" in x for x in disc["analysis"])
    assert result["escalations"]
    assert disc["key"] in result["escalations"][0]["refs"]
    assert result["findings"][0]["refs"] == [disc["key"]]
    assert result["eligibility"]["outcome"] == "eligible_subject_to_verification"
    assert criterion(a, "discrepancies")["status"] == "unknown"

    ack = await case.firm.request(
        "POST",
        f"{case.base}/checks/{disc['key']}/acknowledgement",
        json={"note": "Employer confirmed a one-off £100 salary sacrifice."},
    )
    assert ack.status_code == 201, ack.text
    b = await case.assess()
    assert any(
        c["kind"] == "discrepancy_acknowledged" for c in b["result"]["reassessment"]["changes"]
    )
    assert criterion(b, "discrepancies")["status"] == "met"
    assert "escalate_to_broker" not in tools(b)


# --- 4, 5. multiple income sources and existing commitments ---------------------------------------


async def test_multiple_incomes_are_counted_once_and_commitments_reduce_affordability(
    case: Case,
) -> None:
    await case.payslips()
    await case.statements()
    # A declared salary on the same employment is not added to the documented one.
    await case.observe(
        income_source_id=case.source,
        measure="basic_pay",
        value={"type": "money", "amount_minor": 9_000_000},
        frequency="annual",
        source_kind="declared",
    )
    rental = await case.firm.request(
        "POST",
        f"{case.base}/financial/income-sources",
        json={"applicant_id": case.alex["id"], "kind": "property_rental"},
    )
    assert rental.status_code == 201, rental.text
    rid = rental.json()["id"]
    await case.observe(
        income_source_id=rid,
        measure="gross_rent",
        value={"type": "money", "amount_minor": 100_000},
        frequency="monthly",
    )
    await case.observe(
        income_source_id=rid,
        measure="rental_costs",
        value={"type": "money", "amount_minor": 20_000},
        frequency="monthly",
    )
    loan = await case.firm.request(
        "POST",
        f"{case.base}/financial/commitments",
        json={"kind": "personal_loan", "parties": [{"applicant_id": case.alex["id"]}]},
    )
    assert loan.status_code == 201, loan.text
    await case.observe(
        commitment_id=loan.json()["id"],
        measure="monthly_payment",
        value={"type": "money", "amount_minor": 30_000},
        frequency="monthly",
    )

    a = await case.assess()
    by_kind = {i["kind"]: i for i in a["result"]["income"]}
    employment, letting = by_kind["employment"], by_kind["property_rental"]
    assert employment["declared_gross_monthly_minor"] == 750_000  # kept for comparison only
    assert employment["qualifying_gross_monthly_minor"] == employment["gross_monthly_minor"]
    assert letting["verification"] == "declared"
    assert letting["qualifying_gross_monthly_minor"] == 60_000
    afford = a["result"]["affordability"]
    assert afford["gross_monthly_income_minor"] == employment["gross_monthly_minor"] + 60_000
    assert afford["commitments_monthly_minor"] == 30_000
    assert any(
        g["code"].startswith("income:") and not g["supported_on_platform"]
        for g in a["result"]["evidence_gaps"]
    )
    assert criterion(a, "income_evidence")["status"] == "unknown"  # declared rent isn't verified

    # The same case without the loan affords more (the deterministic engine, read-only).
    out = await case.firm.request(
        "POST",
        f"{case.base}/affordability/scenarios",
        json={"scenarios": [{"label": "Longer term", "term_months": 420}]},
    )
    assert out.status_code == 200, out.text
    body = out.json()
    assert body["base"]["max_mortgage_minor"] == afford["max_mortgage_minor"]
    assert body["scenarios"][0]["changed"] == ["term"]
    assert body["scenarios"][0]["hypothetical"] is True


# --- 6, 7, 8. insufficient affordability, failed criteria, scenarios ------------------------------


async def test_an_unaffordable_loan_is_not_currently_eligible_with_alternatives(
    firm: Firm, worker: Worker, owner: asyncpg.Connection
) -> None:
    case = await _case(firm, worker, loan=240_000, value=300_000)
    await case.payslips()
    await case.statements()
    a = await case.assess()
    e = a["result"]["eligibility"]
    assert e["outcome"] == "not_currently_eligible"
    assert {c["code"] for c in e["criteria"] if c["status"] == "not_met"} >= {
        "income_multiple",
        "affordability",
    }
    assert a["result"]["affordability"]["shortfall_minor"] > 0
    labels = {s["label"] for s in a["result"]["scenarios"]}
    assert "Borrow the maximum indicated" in labels
    assert "Increase the deposit to fit" in labels
    assert all(s["hypothetical"] for s in a["result"]["scenarios"])
    # Scenarios never change the base result.
    assert a["result"]["affordability"]["loan_amount_minor"] == 24_000_000
    assert "escalate_to_broker" in tools(a)


# --- 11. broker decisions and overrides; duplicate assessments ------------------------------------


async def test_broker_decisions_overrides_and_duplicate_runs(
    case: Case, owner: asyncpg.Connection
) -> None:
    await case.payslips()
    a = await case.assess()
    url = f"{case.base}/assessments/{a['id']}/decisions"
    adviser = await case.firm.add_member("adviser")
    no_reason = await case.firm.request(
        "POST",
        url,
        as_=adviser,
        json={"decision": "override", "override_outcome": "potentially_eligible"},
    )
    assert no_reason.status_code == 422
    same = await case.firm.request(
        "POST",
        url,
        as_=adviser,
        json={"decision": "override", "override_outcome": a["outcome"], "reason": "x"},
    )
    assert same.status_code == 422
    ok = await case.firm.request(
        "POST",
        url,
        as_=adviser,
        json={
            "decision": "override",
            "override_outcome": "potentially_eligible",
            "reason": "Statements seen in branch; lender accepts.",
        },
    )
    assert ok.status_code == 201, ok.text
    info = await case.firm.request(
        "POST",
        url,
        json={
            "decision": "request_information",
            "reason": "Need the bonus letter",
            "request_title": "Bonus letter",
            "request_document_type": "letter",
        },
    )
    assert info.status_code == 201, info.text
    read_only = await case.firm.add_member("read_only")
    assert (
        await case.firm.request("POST", url, as_=read_only, json={"decision": "accept"})
    ).status_code == 403

    got = (await case.firm.request("GET", f"{case.base}/assessments/{a['id']}")).json()
    assert got["outcome"] == "eligible_subject_to_verification"  # the agent's result is unchanged
    assert got["final_outcome"] == "potentially_eligible"  # the broker's decision
    assert [d["decision"] for d in got["decisions"]] == ["override", "request_information"]
    assert any(
        r["requested_by"] == "broker" and r["title"] == "Bonus letter"
        for r in await case.requests()
    )
    decided = await owner.fetchval(
        "SELECT count(*) FROM audit_events WHERE case_id = $1 AND action = 'assessment.decided'",
        uuid.UUID(case.id),
    )
    assert decided == 2

    # Nothing changed: the latest result is returned, not re-run. Forcing runs a new version.
    reused = await case.assess(expect=200)
    assert reused["_reused"] is True
    assert reused["id"] == a["id"]
    forced = await case.assess(force=True)
    assert forced["version"] == 2
    # One active assessment per case.
    await owner.execute(
        "INSERT INTO assessments (id, tenant_id, case_id, version, status, policy_code, "
        "planner, trigger, created_at, step_count) "
        "VALUES (gen_random_uuid(), $1, $2, 99, 'queued', 'uk-resi-standard/1', 'pending', "
        "'initial', now(), 0)",
        case.firm.tenant_id,
        uuid.UUID(case.id),
    )
    busy = await case.firm.request("POST", f"{case.base}/assessments", json={"force": True})
    assert busy.status_code == 409
    assert busy.json()["type"].endswith("assessment-in-progress")


# --- 14. versioning, immutability and isolation ---------------------------------------------------


async def test_finished_assessments_are_final_and_isolated(case: Case, make_firm: MakeFirm) -> None:
    await case.payslips(months=1)
    a = await case.assess()
    app = await asyncpg.connect(db_url("meip_app", TEST_DB, driver="postgresql"))
    try:
        async with app.transaction():
            await app.execute(
                "SELECT set_config('app.tenant_id', $1, true)", str(case.firm.tenant_id)
            )
            with pytest.raises(asyncpg.PostgresError):
                await app.execute(
                    "UPDATE assessments SET outcome = 'potentially_eligible' WHERE id = $1",
                    uuid.UUID(a["id"]),
                )
        async with app.transaction():
            await app.execute(
                "SELECT set_config('app.tenant_id', $1, true)", str(case.firm.tenant_id)
            )
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await app.execute(
                    "DELETE FROM assessment_steps WHERE assessment_id = $1", uuid.UUID(a["id"])
                )
    finally:
        await app.close()
    other = await make_firm("Other Brokers Ltd")
    for path in (f"{case.base}/assessments/{a['id']}", f"{case.base}/assessments"):
        assert (await other.request("GET", path)).status_code == 404


# --- 12, 13. failed and duplicate tool calls; the model's planner and its fallback ----------------


class Scripted:
    """A test double standing in for the model: proposes a fixed sequence of calls."""

    name = "scripted"
    model = None

    def __init__(self, calls: list[PlannedCall]):
        self.calls = list(calls)
        self.seen: list[tuple[str, Any, Any]] = []

    async def next(self) -> PlannedCall:
        return self.calls.pop(0)

    def observe(self, call: PlannedCall, output: Any, error: Any) -> None:
        self.seen.append((call.tool, output, error))


async def _queued(owner: asyncpg.Connection, case: Case) -> uuid.UUID:
    aid = uuid.uuid4()
    await owner.execute(
        "INSERT INTO assessments (id, tenant_id, case_id, version, status, policy_code, "
        "planner, trigger, created_at, step_count) "
        "VALUES ($1, $2, $3, 1, 'queued', 'uk-resi-standard/1', 'pending', 'initial', now(), 0)",
        aid,
        case.firm.tenant_id,
        uuid.UUID(case.id),
    )
    return aid


async def test_failed_and_repeated_tool_calls_are_recorded_and_the_run_completes(
    case: Case, worker: Worker, owner: asyncpg.Connection
) -> None:
    await case.payslips(months=1)
    aid = await _queued(owner, case)
    planner = Scripted(
        [
            PlannedCall("drop_tables", {}, "Not a tool."),
            PlannedCall("record_finding", {"title": "x", "severity": "fatal"}, "Invalid input."),
            PlannedCall(
                "generate_evidence_request", {"gap_codes": ["made-up"]}, "Not an identified gap."
            ),
            PlannedCall("validate_income", {}, "First."),
            PlannedCall("validate_income", {}, "Again: returns the recorded output."),
            PlannedCall("generate_assessment_report", {"summary": "Done."}, "Finish."),
        ]
    )
    await agent.run(worker[0], worker[2], case.firm.tenant_id, aid, None, planner=planner)  # type: ignore[arg-type]
    a = (await case.firm.request("GET", f"{case.base}/assessments/{aid}")).json()
    assert a["status"] == "completed", a
    steps = [(s["tool"], s["status"], s["error_code"]) for s in a["steps"]]
    assert steps[:5] == [
        ("drop_tables", "error", "unknown_tool"),
        ("record_finding", "error", "invalid_input"),
        ("generate_evidence_request", "error", "unknown_gap"),
        ("validate_income", "ok", None),
        ("validate_income", "cached", None),
    ]
    assert a["steps"][3]["output"] == a["steps"][4]["output"]
    assert all(error for _, _, error in planner.seen[:3])  # the planner was told each error
    assert (
        await owner.fetchval(
            "SELECT count(*) FROM evidence_requests WHERE case_id = $1", uuid.UUID(case.id)
        )
        == 0
    )


def _openai_settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={
            "agent_planner": "openai",
            "openai_api_key": SecretStr("sk-test"),
            "auth_mode": AuthMode.DEV,
        }
    )


def _fc(name: str, args: dict[str, Any], call_id: str) -> dict[str, Any]:
    return {
        "type": "function_call",
        "name": name,
        "arguments": json.dumps(args),
        "call_id": call_id,
    }


async def test_the_model_planner_drives_a_run_and_falls_back_when_unavailable(
    case: Case, worker: Worker, owner: asyncpg.Connection
) -> None:
    await case.payslips(months=1)
    settings = _openai_settings(worker[2])
    bodies: list[Any] = [
        {
            "status": "completed",
            "output": [_fc("get_assessment_context", {"why": "Start with the case."}, "c1")],
        },
        {
            "status": "completed",
            "output": [_fc("identify_evidence_gaps", {"why": "What's missing?"}, "c2")],
        },
        {
            "status": "completed",
            "output": [
                _fc(
                    "generate_assessment_report",
                    {
                        "summary": "Two payslips are missing.",
                        "recommendations": ["Request payslips."],
                        "why": "Done.",
                    },
                    "c3",
                )
            ],
        },
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=bodies.pop(0))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    aid = await _queued(owner, case)
    await agent.run(
        worker[0],
        settings,
        case.firm.tenant_id,
        aid,
        None,
        planner=OpenAIPlanner(settings, client=client, backoff=0),
    )
    a = (await case.firm.request("GET", f"{case.base}/assessments/{aid}")).json()
    assert a["status"] == "completed"
    assert a["planner"] == "openai"
    assert [s["rationale"] for s in a["steps"]] == [
        "Start with the case.",
        "What's missing?",
        "Done.",
    ]
    assert a["result"]["summary"] == "Two payslips are missing."
    # The outcome comes from the rules, not from the model's text.
    assert a["result"]["eligibility"]["outcome"] == "eligible_subject_to_verification"

    down = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    await owner.execute("UPDATE jobs SET status = 'done'")
    second = uuid.uuid4()
    await owner.execute(
        "INSERT INTO assessments (id, tenant_id, case_id, version, status, policy_code, "
        "planner, trigger, created_at, step_count) "
        "VALUES ($1, $2, $3, 2, 'queued', 'uk-resi-standard/1', 'pending', 'reassessment', "
        "now(), 0)",
        second,
        case.firm.tenant_id,
        uuid.UUID(case.id),
    )
    await agent.run(
        worker[0],
        settings,
        case.firm.tenant_id,
        second,
        None,
        planner=OpenAIPlanner(settings, client=down, backoff=0),
    )
    b = (await case.firm.request("GET", f"{case.base}/assessments/{second}")).json()
    assert b["status"] == "completed"
    assert b["steps"][0]["error_code"] == "planner_fallback"
    assert tools(b)[-1] == "generate_assessment_report"


async def test_an_open_evidence_request_can_be_withdrawn_once_and_is_audited(
    case: Case, owner: asyncpg.Connection
) -> None:
    await case.payslips(months=1)
    await case.assess()
    open_request = next(r for r in await case.requests() if r["status"] == "open")
    url = f"{case.base}/evidence-requests/{open_request['id']}/withdrawal"
    case_admin = await case.firm.add_member("case_administrator")
    assert (await case.firm.request("POST", url, as_=case_admin)).status_code == 403
    done = await case.firm.request("POST", url)
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "withdrawn"
    assert done.json()["closed_at"]
    assert (await case.firm.request("POST", url)).status_code == 409
    closed = await owner.fetchval(
        "SELECT count(*) FROM audit_events WHERE case_id = $1 "
        "AND action = 'evidence.request_closed'",
        uuid.UUID(case.id),
    )
    assert closed == 1
