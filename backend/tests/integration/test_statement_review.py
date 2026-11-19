"""Stage 2-B step 2B-2: broker confirmation of bank statements, accounts and holders.

SYNTHETIC statements only. Confirmation (the extracted information matches the document; which
account; who holds it) is tested separately from validation (the statement's balances)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import asyncpg
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from meip.api.deps import get_settings
from meip.core.config import Environment, Settings
from meip.core.db import create_engine, create_sessionmaker
from meip.documents.processing import run_once
from meip.documents.storage import FilesystemBlobStore
from tests.conftest import TEST_DB, Firm, db_url
from tests.synthetic_docs import (
    SeriesStatement,
    bank_statement_series,
    payslip_series,
    salaries_from,
)

Worker = tuple[async_sessionmaker[AsyncSession], FilesystemBlobStore, Settings]
SORT = "60-71-88"


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


def series(**kw: Any) -> list[SeriesStatement]:
    return bank_statement_series(
        salaries_from(payslip_series(months=4)), **{"sort_code": SORT, **kw}
    )


class Case:
    def __init__(self, firm: Firm, case: dict[str, Any], alex: dict[str, Any], sam: dict[str, Any]):
        self.firm, self.case, self.alex, self.sam = firm, case, alex, sam
        self.base = f"/api/v1/cases/{case['id']}"

    async def statement(self, worker: Worker, s: SeriesStatement) -> dict[str, Any]:
        resp = await self.firm.upload(
            self.case["id"],
            s.document.pdf(),
            applicant_id=self.alex["id"],
            document_type="bank_statement",
        )
        assert resp.status_code == 201, resp.text
        while await run_once(*worker):
            pass
        doc: dict[str, Any] = (
            await self.firm.request("GET", f"{self.base}/documents/{resp.json()['id']}")
        ).json()
        assert doc["status"] == "extracted", doc
        return doc

    async def extraction(self, doc: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = (
            await self.firm.request("GET", f"{self.base}/documents/{doc['id']}/extraction")
        ).json()
        return out

    async def confirm(
        self, doc: dict[str, Any], decisions: list[dict[str, Any]] | None = None, **body: Any
    ) -> Any:
        if decisions is None:
            x = await self.extraction(doc)
            decisions = [{"ordinal": f["ordinal"], "decision": "accept"} for f in x["summary"]]
        return await self.firm.request(
            "POST",
            f"{self.base}/documents/{doc['id']}/review",
            if_match=doc["version"],
            json={"decisions": decisions, **body},
        )

    def new_account(self, *holders: dict[str, Any], label: str | None = None) -> dict[str, Any]:
        return {"new_account": {"holder_ids": [h["id"] for h in holders], "label": label}}

    async def accounts(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = (
            await self.firm.request("GET", f"{self.base}/bank-accounts")
        ).json()
        return out


@pytest.fixture
async def case(firm: Firm, owner: asyncpg.Connection) -> Case:
    c = await firm.create_case()
    alex = await firm.add_applicant(c["id"], first_name="Alex")
    sam = await firm.add_applicant(c["id"], first_name="Sam")
    return Case(firm, c, alex, sam)


def by_code(x: dict[str, Any], code: str) -> dict[str, Any]:
    return next(f for f in x["summary"] if f["field_code"] == code)


# --- confirming to a new account --------------------------------------------------------


async def test_confirm_a_statement_to_a_new_account(
    case: Case, worker: Worker, owner: asyncpg.Connection
) -> None:
    doc = await case.statement(worker, series()[0])
    x = await case.extraction(doc)
    assert x["account_match"] == {
        "status": "new",
        "account_id": None,
        "same_ending_account_ids": [],
    }
    assert x["integrity"]["totals"] == "passed"
    assert x["integrity"]["running"] == "passed"
    observations_before = await owner.fetchval(
        "SELECT count(*) FROM financial_observations WHERE tenant_id = $1", case.firm.tenant_id
    )
    resp = await case.confirm(doc, **case.new_account(case.alex, label="Current account"))
    assert resp.status_code == 201, resp.text
    review = resp.json()
    assert review["income_source_id"] is None
    assert review["frequency"] is None
    account = review["bank_account"]
    assert account["account_ending"] == "4821"
    assert account["label"] == "Current account"
    assert account["fingerprinted"] is True
    assert account["joint"] is False
    assert [h["applicant_id"] for h in account["holders"]] == [case.alex["id"]]
    assert account["statement_document_ids"] == [doc["id"]]
    assert review["integrity"]["totals"] == "passed"
    assert all(d["observation_id"] is None for d in review["decisions"])
    # Confirmation creates no financial figures.
    assert (
        await owner.fetchval(
            "SELECT count(*) FROM financial_observations WHERE tenant_id = $1",
            case.firm.tenant_id,
        )
        == observations_before
    )
    saved = (await case.firm.request("GET", f"{case.base}/documents/{doc['id']}")).json()
    assert saved["status"] == "reviewed"
    again = (await case.firm.request("GET", f"{case.base}/documents/{doc['id']}/review")).json()
    assert again["bank_account"]["id"] == account["id"]


async def test_a_joint_account_and_adding_a_holder(case: Case, worker: Worker) -> None:
    doc = await case.statement(worker, series()[0])
    account = (await case.confirm(doc, **case.new_account(case.alex, case.sam))).json()[
        "bank_account"
    ]
    assert account["joint"] is True
    assert {h["applicant_id"] for h in account["holders"]} == {case.alex["id"], case.sam["id"]}

    doc2 = await case.statement(worker, series(sort_code="40-11-22")[1])
    sole = (await case.confirm(doc2, **case.new_account(case.alex))).json()["bank_account"]
    url = f"{case.base}/bank-accounts/{sole['id']}/holders"
    added = await case.firm.request("POST", url, json={"applicant_id": case.sam["id"]})
    assert added.status_code == 201, added.text
    assert added.json()["joint"] is True
    assert (
        await case.firm.request("POST", url, json={"applicant_id": case.sam["id"]})
    ).status_code == 409
    other = await case.firm.create_case()
    stranger = await case.firm.add_applicant(other["id"])
    assert (
        await case.firm.request("POST", url, json={"applicant_id": stranger["id"]})
    ).status_code == 422


# --- the same account, a different account, an unverified account -----------------------


async def test_the_next_statement_is_matched_to_its_account(case: Case, worker: Worker) -> None:
    first, second = series()[:2]
    doc1 = await case.statement(worker, first)
    account = (await case.confirm(doc1, **case.new_account(case.alex))).json()["bank_account"]
    doc2 = await case.statement(worker, second)
    x = await case.extraction(doc2)
    assert x["account_match"]["status"] == "existing"
    assert x["account_match"]["account_id"] == account["id"]
    # A new account for the same account number is refused, pointing at the existing one.
    refused = await case.confirm(doc2, **case.new_account(case.alex))
    assert refused.status_code == 409
    assert refused.json()["type"] == "urn:meip:problem:account-conflict"
    assert refused.json()["existing_account_id"] == account["id"]
    ok = await case.confirm(doc2, bank_account_id=account["id"])
    assert ok.status_code == 201, ok.text
    assert ok.json()["bank_account"]["statement_document_ids"] == [doc1["id"], doc2["id"]]


async def test_same_last_four_digits_are_different_accounts(case: Case, worker: Worker) -> None:
    doc_a = await case.statement(worker, series()[0])
    a = (await case.confirm(doc_a, **case.new_account(case.alex))).json()["bank_account"]
    doc_b = await case.statement(worker, series(sort_code="40-11-22")[0])
    x = await case.extraction(doc_b)
    assert x["account_match"] == {
        "status": "new",
        "account_id": None,
        "same_ending_account_ids": [a["id"]],
    }
    wrong = await case.confirm(doc_b, bank_account_id=a["id"])
    assert wrong.status_code == 409
    assert "different accounts" in wrong.json()["detail"]
    b = await case.confirm(doc_b, **case.new_account(case.sam))
    assert b.status_code == 201, b.text
    assert [acc["account_ending"] for acc in await case.accounts()] == ["4821", "4821"]


async def test_an_unverified_statement_relies_on_the_broker(case: Case, worker: Worker) -> None:
    doc1 = await case.statement(worker, series()[0])
    account = (await case.confirm(doc1, **case.new_account(case.alex))).json()["bank_account"]
    masked = await case.statement(worker, series(masked_number=True)[1])
    x = await case.extraction(masked)
    assert x["account_match"]["status"] == "unverified"
    assert x["account_match"]["same_ending_account_ids"] == [account["id"]]
    ok = await case.confirm(masked, bank_account_id=account["id"])
    assert ok.status_code == 201, ok.text


async def test_a_new_unverified_account_is_not_fingerprinted(case: Case, worker: Worker) -> None:
    masked = await case.statement(worker, series(masked_number=True)[0])
    account = (await case.confirm(masked, **case.new_account(case.alex))).json()["bank_account"]
    assert account["fingerprinted"] is False


async def test_a_full_number_may_join_an_account_that_was_never_verified(
    case: Case, worker: Worker
) -> None:
    masked = await case.statement(worker, series(masked_number=True)[0])
    unverified = (await case.confirm(masked, **case.new_account(case.alex))).json()["bank_account"]
    full = await case.statement(worker, series()[1])
    x = await case.extraction(full)
    assert x["account_match"]["status"] == "new"
    assert x["account_match"]["same_ending_account_ids"] == [unverified["id"]]
    ok = await case.confirm(full, bank_account_id=unverified["id"])
    assert ok.status_code == 201, ok.text
    assert ok.json()["bank_account"]["fingerprinted"] is False  # accounts are insert-only


async def test_the_account_ending_must_match(case: Case, worker: Worker) -> None:
    doc1 = await case.statement(worker, series(account_number="99997710")[0])
    other = (await case.confirm(doc1, **case.new_account(case.alex))).json()["bank_account"]
    doc2 = await case.statement(worker, series(masked_number=True)[1])
    refused = await case.confirm(doc2, bank_account_id=other["id"])
    assert refused.status_code == 409
    assert "ends 4821" in refused.json()["detail"]


# --- confirmation is not validation -----------------------------------------------------


async def test_a_statement_with_a_balance_break_can_be_confirmed_and_stays_visible(
    case: Case, worker: Worker
) -> None:
    doc = await case.statement(worker, series(positions={"missing_row": 0})[0])
    x = await case.extraction(doc)
    assert x["integrity"]["running"] == "discrepancy"
    assert "unaccounted for" in x["integrity"]["running_message"]
    review = await case.confirm(doc, **case.new_account(case.alex))
    assert review.status_code == 201, review.text
    assert review.json()["integrity"]["running"] == "discrepancy"  # never cleared by confirming
    events = (await case.firm.request("GET", f"{case.base}/events?limit=100")).json()["items"]
    [event] = [e for e in events if e["action"] == "document.reviewed"]
    assert event["changes"]["integrity_running"] == "discrepancy"
    assert event["changes"]["integrity_totals"] == "discrepancy"
    assert event["changes"]["fingerprint_status"] == "new"
    assert event["changes"]["account_created"] is True
    assert event["changes"]["holder_count"] == 1


async def test_a_corrected_balance_is_used_for_the_confirmed_integrity(
    case: Case, worker: Worker
) -> None:
    s = series()[0]
    doc = await case.statement(worker, s)
    x = await case.extraction(doc)
    decisions = [{"ordinal": f["ordinal"], "decision": "accept"} for f in x["summary"]]
    closing = by_code(x, "closing_balance")["ordinal"]
    decisions[closing - 1] = {
        "ordinal": closing,
        "decision": "correct",
        "corrected_money_minor": s.closing_minor + 1_000,
        "reason": "Checked against the paper statement",
    }
    review = await case.confirm(doc, decisions, **case.new_account(case.alex))
    assert review.status_code == 201, review.text
    assert review.json()["integrity"]["totals"] == "discrepancy"
    corrected = next(d for d in review.json()["decisions"] if d["ordinal"] == closing)
    assert corrected["extracted_money_minor"] == s.closing_minor  # the original is kept beside it


# --- refusals ---------------------------------------------------------------------------


async def test_confirmation_refusals(case: Case, worker: Worker) -> None:
    doc = await case.statement(worker, series()[0])
    x = await case.extraction(doc)
    accept = [{"ordinal": f["ordinal"], "decision": "accept"} for f in x["summary"]]
    ending = by_code(x, "account_ending")["ordinal"]
    cases: list[tuple[dict[str, Any], list[dict[str, Any]], int]] = [
        ({}, accept, 422),  # neither account option
        ({**case.new_account(case.alex), "bank_account_id": case.alex["id"]}, accept, 422),
        ({**case.new_account(case.alex), "income_source_id": case.alex["id"]}, accept, 422),
        ({"new_account": {"holder_ids": []}}, accept, 422),
        (case.new_account({"id": "00000000-0000-7000-8000-000000000001"}), accept, 422),
        ({"bank_account_id": "00000000-0000-7000-8000-000000000001"}, accept, 422),
        (case.new_account(case.alex), accept[:-1], 422),  # a field without a decision
        (
            case.new_account(case.alex),
            [
                {**d, "decision": "correct", "corrected_text": "48", "reason": "x"}
                if d["ordinal"] == ending
                else d
                for d in accept
            ],
            422,  # an account ending is 4 digits
        ),
        (
            case.new_account(case.alex),
            [{**d, "decision": "reject"} if d["ordinal"] == ending else d for d in accept],
            422,  # a rejection needs a reason
        ),
        (
            case.new_account(case.alex),
            [
                {**d, "decision": "reject", "reason": "unreadable"} if d["ordinal"] == ending else d
                for d in accept
            ],
            422,  # without the ending, no account can be confirmed
        ),
        (
            case.new_account(case.alex),
            [{**accept[0], "measure": "net_pay"}, *accept[1:]],
            422,  # a statement saves no figures
        ),
    ]
    for body, decisions, status in cases:
        resp = await case.confirm(doc, decisions, **body)
        assert resp.status_code == status, (body, resp.text)
    assert (await case.confirm(doc, **case.new_account(case.alex))).status_code == 201
    again = await case.confirm(doc, **case.new_account(case.alex))
    assert again.status_code == 409
    assert again.json()["type"] == "urn:meip:problem:already-reviewed"


async def test_a_payslip_review_still_needs_an_income_source(case: Case, worker: Worker) -> None:
    resp = await case.firm.upload(
        case.case["id"], payslip_series(months=1)[0].document.pdf(), applicant_id=case.alex["id"]
    )
    while await run_once(*worker):
        pass
    doc = (await case.firm.request("GET", f"{case.base}/documents/{resp.json()['id']}")).json()
    x = await case.extraction(doc)
    fields = [f for s in ("summary", "payments", "deductions", "year_to_date") for f in x[s]]
    decisions = [{"ordinal": f["ordinal"], "decision": "accept"} for f in fields]
    missing = await case.confirm(doc, decisions)
    assert missing.status_code == 422
    assert "income_source_id" in missing.json()["detail"]
    with_account = await case.confirm(doc, decisions, **case.new_account(case.alex))
    assert with_account.status_code == 422


# --- the fail-closed gate ---------------------------------------------------------------


@pytest.fixture
def deployed(app: FastAPI, settings: Settings) -> Iterator[None]:
    production = settings.model_copy(update={"env": Environment.PRODUCTION})
    app.dependency_overrides[get_settings] = lambda: production
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_settings, None)


async def test_account_routes_are_refused_outside_local_and_test(
    case: Case, deployed: None
) -> None:
    resp = await case.firm.request("GET", f"{case.base}/bank-accounts")
    assert resp.status_code == 404
    assert resp.json()["type"] == "urn:meip:problem:feature-disabled"
