"""Stage 3-B: approving the financial profile. SYNTHETIC data only.

Covers the five points of plan revision 2: atomic approval under the case lock, a complete
and self-contained snapshot, readiness semantics, database immutability, and the attestation
wording kept with each approval."""

from __future__ import annotations

import asyncio
import hashlib
import json
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
from meip.profile import attestation
from tests.conftest import TEST_DB, Firm, MakeFirm, db_url
from tests.synthetic_docs import p60, payslip_series

Worker = tuple[async_sessionmaker[AsyncSession], FilesystemBlobStore, Settings]
SECTIONS = ("summary", "payments", "deductions", "year_to_date")


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
    def __init__(self, firm: Firm, case: dict[str, Any], alex: dict[str, Any], source: str):
        self.firm, self.case, self.alex, self.source = firm, case, alex, source
        self.id = case["id"]
        self.base = f"/api/v1/cases/{case['id']}"

    async def version(self) -> int:
        out: int = (await self.firm.request("GET", self.base)).json()["version"]
        return out

    async def move(self, to: str, **body: Any) -> Any:
        return await self.firm.request(
            "POST",
            f"{self.base}/transitions",
            if_match=await self.version(),
            json={"to_status": to, **body},
        )

    async def ready_for_approval(self) -> None:
        for to in ("collecting_evidence", "in_review"):
            resp = await self.move(to)
            assert resp.status_code == 200, resp.text

    async def declare(self, pounds: int, supersedes: str | None = None) -> str:
        body: dict[str, Any] = {
            "income_source_id": self.source,
            "measure": "basic_pay",
            "value": {"type": "money", "amount_minor": pounds * 100},
            "frequency": "annual",
            "source_kind": "declared",
        }
        if supersedes:
            body["supersedes_observation_id"] = supersedes
        resp = await self.firm.request("POST", f"{self.base}/financial/observations", json=body)
        assert resp.status_code == 201, resp.text
        return str(resp.json()["id"])

    async def profile(self) -> dict[str, Any]:
        resp = await self.firm.request("GET", f"{self.base}/profile")
        assert resp.status_code == 200, resp.text
        out: dict[str, Any] = resp.json()
        return out

    async def approve(self, fingerprint: str | None = None, **body: Any) -> Any:
        if fingerprint is None:
            fingerprint = (await self.profile())["fingerprint"]
        return await self.firm.request(
            "POST",
            f"{self.base}/profile/approvals",
            json={
                "fingerprint": fingerprint,
                "attestation_version": attestation.CURRENT,
                "confirmed": True,
                **body,
            },
        )

    async def upload(self, worker: Worker, pdf: bytes) -> dict[str, Any]:
        resp = await self.firm.upload(self.id, pdf, applicant_id=self.alex["id"])
        assert resp.status_code == 201, resp.text
        while await run_once(*worker):
            pass
        out: dict[str, Any] = (
            await self.firm.request("GET", f"{self.base}/documents/{resp.json()['id']}")
        ).json()
        return out

    async def review(self, doc: dict[str, Any]) -> None:
        url = f"{self.base}/documents/{doc['id']}"
        x = (await self.firm.request("GET", f"{url}/extraction")).json()
        resp = await self.firm.request(
            "POST",
            f"{url}/review",
            if_match=doc["version"],
            json={
                "income_source_id": self.source,
                "frequency": "monthly",
                "decisions": [
                    {"ordinal": f["ordinal"], "decision": "accept"} for s in SECTIONS for f in x[s]
                ],
            },
        )
        assert resp.status_code == 201, resp.text


async def _case(firm: Firm) -> Case:
    c = await firm.create_case()
    alex = await firm.add_applicant(c["id"], first_name="Alex", last_name="Example")
    source = await firm.request(
        "POST",
        f"/api/v1/cases/{c['id']}/financial/income-sources",
        json={"applicant_id": alex["id"], "kind": "employment"},
    )
    assert source.status_code == 201, source.text
    return Case(firm, c, alex, source.json()["id"])


@pytest.fixture
async def case(firm: Firm, owner: asyncpg.Connection) -> Case:
    return await _case(firm)


def blockers(resp: Any) -> list[str]:
    return [b["code"] for b in resp.json()["blockers"]]


# --- the approval and its record ------------------------------------------------------------------


async def test_approve_freezes_the_profile_and_verifies_the_case(
    case: Case, owner: asyncpg.Connection
) -> None:
    await case.declare(45_000)
    await case.ready_for_approval()
    profile = await case.profile()
    assert profile["readiness"]["ready"] is True
    assert profile["readiness"]["attestation"] == {
        "version": attestation.CURRENT,
        "text": attestation.ATTESTATIONS[attestation.CURRENT],
    }
    resp = await case.approve(profile["fingerprint"], note="Reviewed with the client")
    assert resp.status_code == 201, resp.text
    approval = resp.json()
    assert approval["version"] == 1
    assert approval["current"] is True
    assert approval["snapshot_sha256"] == profile["fingerprint"]
    assert resp.headers["etag"] == f'"{approval["case_version"]}"'

    row = await owner.fetchrow("SELECT status, approval_id FROM cases WHERE id = $1", case.id)
    assert row is not None
    assert row["status"] == "verified"
    assert str(row["approval_id"]) == approval["id"]
    stored = await owner.fetchrow(
        "SELECT snapshot, snapshot_sha256, attestation_text, attestation_version "
        "FROM profile_approvals WHERE id = $1",
        approval["id"],
    )
    assert stored is not None
    # The stored text is exactly what was hashed, and the hash is the approved fingerprint.
    assert hashlib.sha256(stored["snapshot"].encode()).hexdigest() == stored["snapshot_sha256"]
    assert stored["snapshot_sha256"] == profile["fingerprint"]
    # The exact wording accepted is kept with the approval.
    assert stored["attestation_version"] == attestation.CURRENT
    assert stored["attestation_text"] == attestation.ATTESTATIONS[attestation.CURRENT]
    events = await owner.fetch(
        "SELECT changes FROM audit_events WHERE case_id = $1 AND action = 'profile.approved'",
        case.id,
    )
    assert len(events) == 1
    changes = json.loads(events[0]["changes"])
    assert changes["approval_version"] == 1
    assert changes["snapshot_sha256"] == profile["fingerprint"]
    assert changes["status"] == {"from": "in_review", "to": "verified"}

    # The snapshot is the profile without its derived state fields.
    detail = (
        await case.firm.request("GET", f"{case.base}/profile/approvals/{approval['id']}")
    ).json()
    expected = {
        k: v for k, v in profile.items() if k not in {"readiness", "approval", "fingerprint"}
    }
    assert detail["snapshot"] == expected
    assert detail["attestation_text"] == attestation.ATTESTATIONS[attestation.CURRENT]
    for kind in ("declared", "documentary", "corroborating", "findings"):
        assert kind in detail["snapshot"]["support"][0]

    live = await case.profile()
    assert live["approval"]["status"] == "approved"
    assert live["approval"]["version"] == 1
    assert live["readiness"]["ready"] is False  # verified: no longer in review


async def test_snapshot_members_resolve_every_user_it_references(
    case: Case, worker: Worker, owner: asyncpg.Connection
) -> None:
    [slip] = payslip_series(months=1, faults=frozenset({"arithmetic_error"}), fault_at=0)
    await case.review(await case.upload(worker, slip.document.pdf()))
    await case.declare(45_000)
    checks = (await case.firm.request("GET", f"{case.base}/checks")).json()
    for f in checks["findings"]:
        if f["status"] == "discrepancy":
            resp = await case.firm.request(
                "POST", f"{case.base}/checks/{f['key']}/acknowledgement", json={"note": "Seen"}
            )
            assert resp.status_code == 201, resp.text
    await case.ready_for_approval()
    resp = await case.approve()
    assert resp.status_code == 201, resp.text
    approval_id = resp.json()["id"]

    text = await owner.fetchval("SELECT snapshot FROM profile_approvals WHERE id = $1", approval_id)
    snapshot = json.loads(text)
    referenced: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key.endswith("user_id") and isinstance(value, str):
                    referenced.add(value)
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk({k: v for k, v in snapshot.items() if k != "members"})
    members = {m["user_id"]: m["name"] for m in snapshot["members"]}
    assert referenced
    assert referenced <= set(members)

    # A later rename doesn't change what the approval says.
    await owner.execute(
        "UPDATE memberships SET display_name = 'Renamed Broker' WHERE tenant_id = $1",
        case.firm.tenant_id,
    )
    detail = (await case.firm.request("GET", f"{case.base}/profile/approvals/{approval_id}")).json()
    assert {m["name"] for m in detail["snapshot"]["members"]} == set(members.values())
    assert "Renamed Broker" not in json.dumps(detail["snapshot"])


# --- readiness semantics ----------------------------------------------------------------------


async def test_blockers_are_genuine_outstanding_work(case: Case, worker: Worker) -> None:
    # Not in review.
    resp = await case.approve()
    assert resp.status_code == 409
    assert resp.json()["type"].endswith("approval-blocked")
    assert blockers(resp) == ["case_not_in_review"]

    # A document extracted but not reviewed.
    [slip] = payslip_series(months=1)
    doc = await case.upload(worker, slip.document.pdf())
    await case.ready_for_approval()
    resp = await case.approve()
    assert blockers(resp) == ["documents_outstanding"]
    assert resp.json()["blockers"][0]["ids"] == [doc["id"]]
    profile = await case.profile()
    assert [b["code"] for b in profile["readiness"]["blockers"]] == ["documents_outstanding"]


async def test_an_unacknowledged_discrepancy_blocks_until_acknowledged(
    case: Case, worker: Worker
) -> None:
    [slip] = payslip_series(months=1, faults=frozenset({"arithmetic_error"}), fault_at=0)
    await case.review(await case.upload(worker, slip.document.pdf()))
    await case.ready_for_approval()
    resp = await case.approve()
    assert blockers(resp) == ["discrepancies_unacknowledged"]
    [key] = resp.json()["blockers"][0]["ids"]
    stale = (await case.profile())["fingerprint"]
    ack = await case.firm.request(
        "POST", f"{case.base}/checks/{key}/acknowledgement", json={"note": "Employer error"}
    )
    assert ack.status_code == 201, ack.text
    # The acknowledgement changed the profile: the old fingerprint is refused.
    resp = await case.approve(stale)
    assert resp.status_code == 409
    assert resp.json()["type"].endswith("profile-changed")
    assert (await case.approve()).status_code == 201


async def test_absent_positions_never_block(case: Case, worker: Worker) -> None:
    """An income source with no figures, no commitments, no expenditure and an unrecognised
    document are all approvable; the missing figures are a notice."""
    unrecognised = await case.upload(worker, p60().pdf())
    assert unrecognised["status"] == "unrecognised"
    await case.ready_for_approval()
    profile = await case.profile()
    assert profile["readiness"]["ready"] is True
    assert profile["readiness"]["blockers"] == []
    assert profile["readiness"]["notices"] == [
        {"message": "Employment income (Alex Example): no current figures."}
    ]
    assert (await case.approve(profile["fingerprint"])).status_code == 201


async def test_nothing_recorded_blocks(firm: Firm, owner: asyncpg.Connection) -> None:
    c = await firm.create_case()
    await firm.add_applicant(c["id"])
    empty = Case(firm, c, {}, "")
    await empty.ready_for_approval()
    resp = await empty.approve()
    assert blockers(resp) == ["nothing_recorded"]


# --- the attestation --------------------------------------------------------------------------


async def test_the_attestation_must_be_current_confirmed_and_server_worded(case: Case) -> None:
    await case.declare(45_000)
    await case.ready_for_approval()
    assert (await case.approve(attestation_version="attestation/0")).status_code == 422
    assert (await case.approve(confirmed=False)).status_code == 422
    # The client can't supply the wording.
    resp = await case.approve(attestation_text="I agree to anything")
    assert resp.status_code == 422


# --- reopening, superseding, closing -------------------------------------------------------------


async def test_reopening_supersedes_and_the_old_version_stays_intact(
    case: Case, owner: asyncpg.Connection
) -> None:
    figure = await case.declare(45_000)
    await case.ready_for_approval()
    first = (await case.approve()).json()
    v1_text = await owner.fetchval(
        "SELECT snapshot FROM profile_approvals WHERE id = $1", first["id"]
    )

    # Reopening needs a reason; it clears the pointer, keeps the row, records what it superseded.
    assert (await case.move("in_review")).status_code == 422
    resp = await case.move("in_review", note="Client sent a new payslip")
    assert resp.status_code == 200, resp.text
    row = await owner.fetchrow("SELECT status, approval_id FROM cases WHERE id = $1", case.id)
    assert row is not None
    assert (row["status"], row["approval_id"]) == ("in_review", None)
    changes = json.loads(
        await owner.fetchval(
            "SELECT changes FROM audit_events WHERE case_id = $1 "
            "AND action = 'case.status_changed' ORDER BY occurred_at DESC LIMIT 1",
            case.id,
        )
    )
    assert changes["superseded_approval_id"] == first["id"]
    live = await case.profile()
    assert live["approval"]["status"] == "not_approved"
    assert [s["version"] for s in live["approval"]["superseded"]] == [1]
    assert live["approval"]["superseded"][0]["superseded_at"] is not None

    # Change the profile and approve again: version 2.
    await case.declare(46_000, supersedes=figure)
    second = (await case.approve()).json()
    assert second["version"] == 2
    listed = (await case.firm.request("GET", f"{case.base}/profile/approvals")).json()
    assert [(a["version"], a["current"]) for a in listed] == [(2, True), (1, False)]
    assert listed[1]["superseded_at"] is not None

    # Version 1 is unchanged: same text, and it still holds the old value.
    assert (
        await owner.fetchval("SELECT snapshot FROM profile_approvals WHERE id = $1", first["id"])
        == v1_text
    )
    old = (await case.firm.request("GET", f"{case.base}/profile/approvals/{first['id']}")).json()
    assert [f["value"]["amount_minor"] for f in old["snapshot"]["figures"]] == [4_500_000]
    assert old["current"] is False


async def test_closing_keeps_the_final_approval_and_reopening_clears_it(
    case: Case, owner: asyncpg.Connection
) -> None:
    await case.declare(45_000)
    await case.ready_for_approval()
    approval = (await case.approve()).json()
    resp = await case.move("closed", closure_reason="completed")
    assert resp.status_code == 200, resp.text
    row = await owner.fetchrow("SELECT status, approval_id FROM cases WHERE id = $1", case.id)
    assert row is not None
    assert (row["status"], str(row["approval_id"])) == ("closed", approval["id"])
    resp = await case.move("collecting_evidence", note="Remortgage, reopened")
    assert resp.status_code == 200, resp.text
    assert await owner.fetchval("SELECT approval_id FROM cases WHERE id = $1", case.id) is None
    assert (
        await owner.fetchval("SELECT count(*) FROM profile_approvals WHERE case_id = $1", case.id)
        == 1
    )


async def test_the_status_route_never_verifies(case: Case) -> None:
    await case.declare(45_000)
    await case.ready_for_approval()
    resp = await case.move("verified")
    assert resp.status_code == 409
    assert "Approve the profile" in resp.json()["detail"]


# --- the database on its own ------------------------------------------------------------------


async def test_the_runtime_role_cannot_change_or_delete_approvals(case: Case) -> None:
    await case.declare(45_000)
    await case.ready_for_approval()
    approval = (await case.approve()).json()
    app = await asyncpg.connect(db_url("meip_app", TEST_DB, driver="postgresql"))
    try:
        for sql in (
            "UPDATE profile_approvals SET note = 'changed' WHERE id = $1",
            "DELETE FROM profile_approvals WHERE id = $1",
        ):
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await app.execute(sql, approval["id"])
    finally:
        await app.close()


async def test_status_and_pointer_cannot_become_inconsistent(
    firm: Firm, case: Case, owner: asyncpg.Connection
) -> None:
    await case.declare(45_000)
    await case.ready_for_approval()
    approval = (await case.approve()).json()
    # Verified without an approval.
    with pytest.raises(asyncpg.CheckViolationError):
        await owner.execute("UPDATE cases SET approval_id = NULL WHERE id = $1", case.id)
    # An approval on a case in progress.
    with pytest.raises(asyncpg.CheckViolationError):
        await owner.execute("UPDATE cases SET status = 'in_review' WHERE id = $1", case.id)
    # Another case's approval.
    other = await _case(firm)
    await other.ready_for_approval()
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await owner.execute(
            "UPDATE cases SET status = 'verified', approval_id = $1 WHERE id = $2",
            approval["id"],
            other.id,
        )
    # A duplicate version.
    with pytest.raises(asyncpg.UniqueViolationError):
        await owner.execute(
            "INSERT INTO profile_approvals (id, tenant_id, case_id, version, profile_rules, "
            "snapshot, snapshot_sha256, attestation_version, attestation_text, approved_at) "
            "SELECT gen_random_uuid(), tenant_id, case_id, 1, profile_rules, snapshot, "
            "snapshot_sha256, attestation_version, attestation_text, now() "
            "FROM profile_approvals WHERE id = $1",
            approval["id"],
        )


async def test_a_tampered_snapshot_is_refused(case: Case, owner: asyncpg.Connection) -> None:
    await case.declare(45_000)
    await case.ready_for_approval()
    approval = (await case.approve()).json()
    await owner.execute(
        "UPDATE profile_approvals SET snapshot = snapshot || ' ' WHERE id = $1", approval["id"]
    )
    resp = await case.firm.request("GET", f"{case.base}/profile/approvals/{approval['id']}")
    assert resp.status_code == 409
    assert resp.json()["type"].endswith("snapshot-integrity")


# --- concurrency (real transactions) -------------------------------------------------------------


async def test_two_approvals_at_once_give_exactly_one(
    case: Case, owner: asyncpg.Connection
) -> None:
    await case.declare(45_000)
    await case.ready_for_approval()
    fingerprint = (await case.profile())["fingerprint"]
    first, second = await asyncio.gather(case.approve(fingerprint), case.approve(fingerprint))
    codes = sorted([first.status_code, second.status_code])
    assert codes == [201, 409], (first.text, second.text)
    loser = first if first.status_code == 409 else second
    assert blockers(loser) == ["case_not_in_review"]
    assert (
        await owner.fetchval("SELECT count(*) FROM profile_approvals WHERE case_id = $1", case.id)
        == 1
    )


async def test_approve_against_a_status_change_stays_consistent(
    case: Case, owner: asyncpg.Connection
) -> None:
    await case.declare(45_000)
    await case.ready_for_approval()
    fingerprint = (await case.profile())["fingerprint"]
    version = await case.version()
    await asyncio.gather(
        case.approve(fingerprint),
        case.firm.request(
            "POST",
            f"{case.base}/transitions",
            if_match=version,
            json={"to_status": "collecting_evidence"},
        ),
    )
    row = await owner.fetchrow("SELECT status, approval_id FROM cases WHERE id = $1", case.id)
    assert row is not None
    assert (row["status"] == "verified") == (row["approval_id"] is not None)


async def test_a_change_holding_the_case_lock_makes_the_approval_wait_then_refuse(
    case: Case, owner: asyncpg.Connection
) -> None:
    await case.declare(45_000)
    await case.ready_for_approval()
    fingerprint = (await case.profile())["fingerprint"]
    writer = await asyncpg.connect(db_url("meip_owner", TEST_DB, driver="postgresql"))
    try:
        tx = writer.transaction()
        await tx.start()
        await writer.execute("SELECT id FROM cases WHERE id = $1 FOR UPDATE", case.id)
        await writer.execute(
            "UPDATE applicants SET first_name = 'Alexandra' WHERE id = $1", case.alex["id"]
        )
        pending = asyncio.ensure_future(case.approve(fingerprint))
        await asyncio.sleep(1.0)
        assert not pending.done(), "the approval must wait for the case lock"
        await tx.commit()
        resp = await asyncio.wait_for(pending, timeout=30)
    finally:
        await writer.close()
    assert resp.status_code == 409
    assert resp.json()["type"].endswith("profile-changed")
    assert (
        await owner.fetchval("SELECT count(*) FROM profile_approvals WHERE case_id = $1", case.id)
        == 0
    )


async def test_another_firm_cannot_read_or_approve(case: Case, make_firm: MakeFirm) -> None:
    await case.declare(45_000)
    await case.ready_for_approval()
    other = await make_firm()
    assert (await other.request("GET", f"{case.base}/profile/approvals")).status_code == 404
    resp = await other.request(
        "POST",
        f"{case.base}/profile/approvals",
        json={
            "fingerprint": "0" * 64,
            "attestation_version": attestation.CURRENT,
            "confirmed": True,
        },
    )
    assert resp.status_code == 404
