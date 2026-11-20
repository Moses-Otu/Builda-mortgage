"""Stage 2-B step 2B-3: the bank checks (``bank-checks/1``) over real confirmed (SYNTHETIC)
statements and reviewed payslips, through the worker and the API.

The pure rules are tested outcome by outcome in ``tests/unit/test_bank_checks.py``; these
tests prove the loading (confirmed values, accounts, holders, rows), the API shape, evidence
links, acknowledgements and isolation."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import asyncpg
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from meip.core.config import Settings
from meip.core.db import create_engine, create_sessionmaker
from meip.documents.processing import run_once
from meip.documents.storage import FilesystemBlobStore
from tests.conftest import TEST_DB, Firm, MakeFirm, db_url
from tests.synthetic_docs import bank_statement_series, payslip_series, salaries_from

Worker = tuple[async_sessionmaker[AsyncSession], FilesystemBlobStore, Settings]


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
        self, firm: Firm, case: dict[str, Any], alex: dict[str, Any], sam: dict[str, Any], src: str
    ):
        self.firm, self.case, self.alex, self.sam, self.source = firm, case, alex, sam, src
        self.base = f"/api/v1/cases/{case['id']}"

    async def _upload(
        self, worker: Worker, pdf: bytes, applicant: dict[str, Any], kind: str
    ) -> dict[str, Any]:
        resp = await self.firm.upload(
            self.case["id"], pdf, applicant_id=applicant["id"], document_type=kind
        )
        assert resp.status_code == 201, resp.text
        while await run_once(*worker):
            pass
        url = f"{self.base}/documents/{resp.json()['id']}"
        doc: dict[str, Any] = (await self.firm.request("GET", url)).json()
        assert doc["status"] == "extracted", doc
        return doc

    async def _accept_all(
        self, doc: dict[str, Any], sections: tuple[str, ...], **body: Any
    ) -> dict[str, Any]:
        url = f"{self.base}/documents/{doc['id']}"
        x = (await self.firm.request("GET", f"{url}/extraction")).json()
        decisions = [
            {"ordinal": f["ordinal"], "decision": "accept"} for s in sections for f in x[s]
        ]
        resp = await self.firm.request(
            "POST",
            f"{url}/review",
            if_match=doc["version"],
            json={"decisions": decisions, **body},
        )
        assert resp.status_code == 201, resp.text
        out: dict[str, Any] = resp.json()
        return out

    async def payslips(self, worker: Worker) -> None:
        for slip in payslip_series(months=4):
            doc = await self._upload(worker, slip.document.pdf(), self.alex, "payslip")
            await self._accept_all(
                doc,
                ("summary", "payments", "deductions", "year_to_date"),
                income_source_id=self.source,
                frequency="monthly",
            )

    async def statements(
        self, worker: Worker, filed_by: dict[str, Any], holders: list[dict[str, Any]], **kw: Any
    ) -> list[dict[str, Any]]:
        """Upload and confirm a statement series: the first to a new account with
        ``holders``, the rest to the account the fingerprint suggests."""
        docs = []
        for s in bank_statement_series(salaries_from(payslip_series(months=4)), **kw):
            doc = await self._upload(worker, s.document.pdf(), filed_by, "bank_statement")
            x = (
                await self.firm.request("GET", f"{self.base}/documents/{doc['id']}/extraction")
            ).json()
            match = x["account_match"]
            account = (
                {"bank_account_id": match["account_id"]}
                if match["status"] == "existing"
                else {"new_account": {"holder_ids": [h["id"] for h in holders], "label": None}}
            )
            await self._accept_all(doc, ("summary",), **account)
            docs.append(doc)
        return docs

    async def checks(self) -> dict[str, Any]:
        resp = await self.firm.request("GET", f"{self.base}/checks")
        assert resp.status_code == 200, resp.text
        out: dict[str, Any] = resp.json()
        return out


@pytest.fixture
async def case(firm: Firm, owner: asyncpg.Connection) -> Case:
    c = await firm.create_case()
    alex = await firm.add_applicant(c["id"], first_name="Alex", last_name="Example")
    sam = await firm.add_applicant(c["id"], first_name="Sam", last_name="Synthetic")
    source = await firm.request(
        "POST",
        f"/api/v1/cases/{c['id']}/financial/income-sources",
        json={"applicant_id": alex["id"], "kind": "employment"},
    )
    assert source.status_code == 201, source.text
    return Case(firm, c, alex, sam, source.json()["id"])


def bank(checks: dict[str, Any], rule: str) -> list[dict[str, Any]]:
    """A rule's findings, in statement or pay-date order (the API sorts by status first)."""
    found = [f for f in checks["findings"] if f["rule_code"] == rule]
    for f in found:
        assert f["ruleset_version"] == "bank-checks/1"
    return sorted(found, key=lambda f: f["title"])


def salary_by_month(checks: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {f["title"].split(" salary")[0]: f for f in bank(checks, "SALARY-CREDIT")}


async def test_a_joint_account_series_gives_every_bank_check(case: Case, worker: Worker) -> None:
    await case.payslips(worker)
    docs = await case.statements(
        worker,
        case.alex,
        [case.alex, case.sam],
        faults=frozenset({"near_credit", "missing_row"}),
        positions={"near_credit": 1, "missing_row": 2},  # June's salary; July's statement
    )
    checks = await case.checks()
    assert checks["ruleset_versions"] == ["payslip-checks/1", "bank-checks/1"]
    assert checks["parameters"]["bank-checks/1"]["near_match_band_pct"] == "10.0"

    totals = {f["document_id"]: f["status"] for f in bank(checks, "BANK-TOTALS")}
    running = {f["document_id"]: f for f in bank(checks, "BANK-RUNNING")}
    july = docs[2]["id"]
    assert totals == {d["id"]: ("discrepancy" if d["id"] == july else "passed") for d in docs}
    assert running[july]["status"] == "discrepancy"
    assert "a row may be missing or misread" in running[july]["message"]
    broken = [e for e in running[july]["evidence"] if e["source_kind"] == "transaction"]
    assert broken
    assert broken[0]["document_id"] == july
    assert broken[0]["row_number"] > 0
    assert [f["status"] for f in bank(checks, "BANK-GAP")] == ["passed"] * 3

    salary = salary_by_month(checks)
    may = salary["May 2026"]
    assert (may["status"], may["match_outcome"]) == ("passed", "exact")
    assert "(joint: Alex Example and Sam Synthetic)" in may["message"]
    assert "statement filed under Alex Example" in may["message"]
    rel = may["relationship"]
    assert rel["joint"] is True
    assert rel["applicant_holds_account"] is True
    assert set(rel["holder_ids"]) == {case.alex["id"], case.sam["id"]}
    assert rel["document_id"] == docs[0]["id"]
    credit = next(e for e in may["evidence"] if e["source_kind"] == "transaction")
    assert credit["value_text"] == "SERIES EMPLOYER LTD\nSALARY"  # verbatim
    assert credit["row_number"] == rel["row_number"]
    assert any(e["source_kind"] == "document" for e in may["evidence"])  # the payslip's net pay

    june = salary["June 2026"]
    assert (june["status"], june["match_outcome"]) == ("discrepancy", "near")
    assert june["amounts"]["difference"] == -150
    assert june["relationship"] is None
    assert (salary["July 2026"]["status"], salary["July 2026"]["match_outcome"]) == (
        "passed",
        "exact",
    )
    assert salary["August 2026"]["status"] == "not_run"  # the window runs into September

    # 3-A2: only the exact credit into a held account corroborates; the near match is a
    # finding. Statement integrity findings belong to accounts, not to the income source.
    profile = (await case.firm.request("GET", f"{case.base}/profile")).json()
    [support] = [s for s in profile["support"] if s["item_id"] == case.source]
    corroborated = {c["finding_key"] for c in support["corroborating"]}
    assert corroborated == {may["key"], salary["July 2026"]["key"]}
    [may_c] = [c for c in support["corroborating"] if c["finding_key"] == may["key"]]
    assert may_c["statement_document_id"] == docs[0]["id"]
    assert may_c["joint"] is True
    assert may_c["account_ending"] == "4821"
    assert "does not confirm contractual basic salary" in may_c["scope"]
    assert june["key"] in {f["key"] for f in support["findings"]["discrepancies"]}
    item_rules = {f["rule_code"] for kind in support["findings"].values() for f in kind}
    assert not item_rules & {"BANK-TOTALS", "BANK-RUNNING", "BANK-GAP"}

    # Acknowledging a bank discrepancy keeps it a discrepancy and records its rule set.
    resp = await case.firm.request(
        "POST",
        f"{case.base}/checks/{june['key']}/acknowledgement",
        json={"note": "Employer confirmed a £1.50 deduction"},
    )
    assert resp.status_code == 201, resp.text
    after = salary_by_month(await case.checks())["June 2026"]
    assert after["status"] == "discrepancy"
    assert after["acknowledgement"]["note"] == "Employer confirmed a £1.50 deduction"


async def test_acknowledgement_records_the_bank_rule_set(
    case: Case, worker: Worker, owner: asyncpg.Connection
) -> None:
    await case.payslips(worker)
    await case.statements(worker, case.alex, [case.alex], faults=frozenset({"missing_row"}))
    finding = next(f for f in bank(await case.checks(), "BANK-TOTALS") if f["status"] != "passed")
    counts = (
        "SELECT (SELECT count(*) FROM financial_observations WHERE case_id = $1), "
        "(SELECT count(*) FROM document_reviews WHERE case_id = $1), "
        "(SELECT count(*) FROM bank_account_holders WHERE case_id = $1)"
    )
    before = await owner.fetchrow(counts, case.case["id"])
    resp = await case.firm.request(
        "POST", f"{case.base}/checks/{finding['key']}/acknowledgement", json={"note": "Seen"}
    )
    assert resp.status_code == 201, resp.text
    assert await owner.fetchrow(counts, case.case["id"]) == before
    row = await owner.fetchrow(
        "SELECT rule_code, ruleset_version FROM check_acknowledgements WHERE finding_key = $1",
        finding["key"],
    )
    assert row is not None
    assert dict(row) == {"rule_code": "BANK-TOTALS", "ruleset_version": "bank-checks/1"}
    changes = await owner.fetchval(
        "SELECT changes FROM audit_events WHERE action = 'check.acknowledged' AND case_id = $1",
        case.case["id"],
    )
    assert '"ruleset_version": "bank-checks/1"' in changes
    assert finding["bank_account_id"] in changes


async def test_a_partners_sole_account_with_a_gap_and_a_duplicate_credit(
    case: Case, worker: Worker
) -> None:
    await case.payslips(worker)
    await case.statements(
        worker,
        case.sam,
        [case.sam],
        months=5,  # May to September statements, without August
        faults=frozenset({"statement_gap", "duplicate_amount_credit"}),
        positions={"statement_gap": 3, "duplicate_amount_credit": 1},  # June's salary twice
    )
    checks = await case.checks()
    gaps = bank(checks, "BANK-GAP")
    assert [g["status"] for g in gaps].count("discrepancy") == 1
    gap = next(g for g in gaps if g["status"] == "discrepancy")
    assert "No statement covers 1 Aug 2026 to 31 Aug 2026 (31 days)" in gap["message"]

    salary = salary_by_month(checks)
    may = salary["May 2026"]
    assert (may["status"], may["match_outcome"]) == ("discrepancy", "exact")
    assert "held only by Sam Synthetic" in may["message"]
    assert "Alex Example (the payslip's applicant) isn't a recorded holder" in may["message"]
    assert may["relationship"]["applicant_holds_account"] is False
    assert may["relationship"]["filing_applicant_id"] == case.sam["id"]
    june = salary["June 2026"]
    assert (june["status"], june["match_outcome"]) == ("discrepancy", "multiple")
    assert "TRANSFER FROM J SAMPLE" in june["message"]
    # July's and August's windows reach into the missing August statement.
    assert salary["July 2026"]["status"] == salary["August 2026"]["status"] == "not_run"

    # 3-A2: an exact credit into an account Alex doesn't hold, and multiple credits, are
    # findings, never corroboration.
    profile = (await case.firm.request("GET", f"{case.base}/profile")).json()
    [support] = [s for s in profile["support"] if s["item_id"] == case.source]
    assert support["corroborating"] == []
    open_keys = {f["key"] for f in support["findings"]["discrepancies"]}
    assert {may["key"], june["key"]} <= open_keys

    # Adding Alex as a holder changes the relationship, the outcome and the key.
    account_id = may["relationship"]["account_id"]
    resp = await case.firm.request(
        "POST",
        f"{case.base}/bank-accounts/{account_id}/holders",
        json={"applicant_id": case.alex["id"]},
    )
    assert resp.status_code == 201, resp.text
    now = salary_by_month(await case.checks())["May 2026"]
    assert (now["status"], now["match_outcome"]) == ("passed", "exact")
    assert "(joint: Sam Synthetic and Alex Example)" in now["message"]
    assert now["key"] != may["key"]
    profile = (await case.firm.request("GET", f"{case.base}/profile")).json()
    [support] = [s for s in profile["support"] if s["item_id"] == case.source]
    assert [c["finding_key"] for c in support["corroborating"]] == [now["key"]]


async def test_another_firm_cannot_read_or_acknowledge_bank_findings(
    case: Case, worker: Worker, make_firm: MakeFirm
) -> None:
    await case.payslips(worker)
    await case.statements(worker, case.alex, [case.alex], months=2)
    key = bank(await case.checks(), "SALARY-CREDIT")[0]["key"]
    other = await make_firm()
    assert (await other.request("GET", f"{case.base}/checks")).status_code == 404
    resp = await other.request(
        "POST", f"{case.base}/checks/{key}/acknowledgement", json={"note": "x"}
    )
    assert resp.status_code == 404
