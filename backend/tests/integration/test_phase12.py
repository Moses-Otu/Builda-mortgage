"""Phase 1.2: measure governance, qualifiers, applicability, supersession, current values,
withdrawal and audit-detail binding. Rules are checked through the API *and*, where the
database is meant to enforce them, with raw SQL as the runtime or owner role."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Coroutine
from typing import Any

import asyncpg
import pytest

from meip.audit import service as audit_service
from tests.conftest import TEST_DB, Firm, db_url


def money(minor: int, kind: str = "money") -> dict[str, Any]:
    return {"type": kind, "amount_minor": minor, "currency": "GBP"}


class Case:
    def __init__(self, firm: Firm, case_id: str, applicant: dict[str, Any]) -> None:
        self.firm, self.id, self.applicant = firm, case_id, applicant
        self.base = f"/api/v1/cases/{case_id}/financial"

    async def post(self, path: str, body: dict[str, Any], **kw: Any) -> Any:
        return await self.firm.request("POST", f"{self.base}/{path}", json=body, **kw)

    async def get(self, path: str, **params: Any) -> Any:
        return (await self.firm.request("GET", f"{self.base}/{path}", params=params)).json()

    async def income(self, kind: str = "employment") -> dict[str, Any]:
        resp = await self.post(
            "income-sources", {"applicant_id": self.applicant["id"], "kind": kind}
        )
        assert resp.status_code == 201, resp.text
        out: dict[str, Any] = resp.json()
        return out

    async def commitment(self, kind: str = "credit_card") -> dict[str, Any]:
        resp = await self.post(
            "commitments", {"kind": kind, "parties": [{"applicant_id": self.applicant["id"]}]}
        )
        assert resp.status_code == 201, resp.text
        out: dict[str, Any] = resp.json()
        return out

    async def observe(self, **body: Any) -> Any:
        return await self.post("observations", body)


@pytest.fixture
async def case(firm: Firm) -> Case:
    created = await firm.create_case()
    applicant = await firm.add_applicant(created["id"])
    return Case(firm, created["id"], applicant)


async def _connect(role: str, tenant_id: uuid.UUID | None = None) -> asyncpg.Connection:
    conn = await asyncpg.connect(db_url(role, TEST_DB, driver="postgresql"))
    if tenant_id is not None:
        await conn.execute("SELECT set_config('app.tenant_id', $1, false)", str(tenant_id))
    return conn


class Racer:
    """A runtime-role connection with its own open transaction, for races that do not go
    through the service (and so not through its case lock)."""

    def __init__(self, conn: asyncpg.Connection) -> None:
        self.conn = conn
        self.tx = conn.transaction()


@pytest.fixture
async def racers(case: Case) -> AsyncIterator[tuple[Racer, Racer]]:
    pair = (
        Racer(await _connect("meip_app", case.firm.tenant_id)),
        Racer(await _connect("meip_app", case.firm.tenant_id)),
    )
    for racer in pair:
        await racer.tx.start()
    try:
        yield pair
    finally:
        for racer in pair:
            await racer.conn.close()  # rolls back anything left open


async def _blocks(awaitable: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
    """Start a statement and check it is waiting on a lock rather than finishing."""
    task = asyncio.create_task(awaitable)
    await asyncio.sleep(0.5)
    assert not task.done(), "expected the statement to wait for the other transaction"
    return task


@pytest.fixture
async def owner() -> AsyncIterator[asyncpg.Connection]:
    conn = await _connect("meip_owner")
    try:
        yield conn
    finally:
        await conn.close()


@pytest.fixture
async def app_conn(case: Case) -> AsyncIterator[asyncpg.Connection]:
    conn = await _connect("meip_app", case.firm.tenant_id)
    try:
        yield conn
    finally:
        await conn.close()


# --- 1. measure definitions: immutable meaning -------------------------------------------


class TestMeasureGovernance:
    @pytest.mark.parametrize(
        "statement",
        [
            "UPDATE measure_definitions SET description = 'x' WHERE code = 'bonus'",
            "UPDATE measure_definitions SET value_type = 'rate' WHERE code = 'gross_rent'",
            "UPDATE measure_definitions SET qualifier_required = true WHERE code = 'turnover'",
            "DELETE FROM measure_definitions WHERE code = 'investment_income'",
            "UPDATE measure_qualifiers SET description = 'x' WHERE qualifier = 'car'",
            "DELETE FROM measure_qualifiers WHERE qualifier = 'travel'",
        ],
    )
    async def test_meaning_cannot_change_even_for_the_owner(
        self, owner: asyncpg.Connection, statement: str
    ) -> None:
        with pytest.raises(asyncpg.IntegrityConstraintViolationError, match=r"immutable|deleted"):
            await owner.execute(statement)

    @pytest.mark.parametrize(
        "statement",
        [
            "INSERT INTO measure_definitions (code, subject_kind, value_type, temporal_kind,"
            " description) VALUES ('test_x', 'income_source', 'money', 'flow', 'x')",
            "UPDATE measure_definitions SET deprecated_at = now() WHERE code = 'bonus'",
            "DELETE FROM measure_definitions WHERE code = 'bonus'",
            "INSERT INTO measure_qualifiers (measure_code, subject_kind, qualifier, description)"
            " VALUES ('bonus', 'income_source', 'test_x', 'x')",
            "UPDATE measure_qualifiers SET deprecated_at = now() WHERE qualifier = 'car'",
            "DELETE FROM measure_qualifiers WHERE qualifier = 'car'",
            "TRUNCATE measure_qualifiers",
        ],
    )
    async def test_runtime_role_cannot_touch_reference_data(
        self, app_conn: asyncpg.Connection, statement: str
    ) -> None:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await app_conn.execute(statement)

    async def test_deprecation_once_and_only_new_values_are_refused(
        self, case: Case, owner: asyncpg.Connection, app_conn: asyncpg.Connection
    ) -> None:
        code = f"test_{uuid.uuid4().hex[:12]}"
        await owner.execute(
            "INSERT INTO measure_definitions (code, subject_kind, value_type, temporal_kind,"
            " description, qualifier_required) VALUES ($1, 'income_source', 'money', 'flow',"
            " 'Test measure', false)",
            code,
        )
        source = await case.income("other")
        before = await case.observe(
            income_source_id=source["id"], measure=code, value=money(1), frequency="monthly"
        )
        assert before.status_code == 201, before.text
        await owner.execute(
            "UPDATE measure_definitions SET deprecated_at = now() WHERE code = $1", code
        )
        with pytest.raises(asyncpg.IntegrityConstraintViolationError):
            await owner.execute(
                "UPDATE measure_definitions SET deprecated_at = NULL WHERE code = $1", code
            )
        with pytest.raises(asyncpg.IntegrityConstraintViolationError):
            await owner.execute(
                "UPDATE measure_definitions SET deprecated_at = now() WHERE code = $1", code
            )

        resp = await case.observe(
            income_source_id=source["id"], measure=code, value=money(2), frequency="annual"
        )
        assert resp.status_code == 422
        assert "deprecated" in resp.json()["detail"]
        # Existing values keep their meaning and stay current and readable.
        assert [o["id"] for o in await case.get("observations")] == [before.json()["id"]]
        # The database refuses new values too, not only the service.
        with pytest.raises(asyncpg.IntegrityConstraintViolationError, match="deprecated"):
            await app_conn.execute(
                "INSERT INTO financial_observations (id, tenant_id, case_id, income_source_id,"
                " measure, qualifier_required, value_type, temporal_kind, money_minor, currency,"
                " frequency, source_kind, recorded_at) VALUES (gen_random_uuid(), $1, $2, $3,"
                " $4, false, 'money', 'flow', 1, 'GBP', 'weekly', 'declared', now())",
                case.firm.tenant_id,
                uuid.UUID(case.id),
                uuid.UUID(source["id"]),
                code,
            )

        listed = (await case.firm.request("GET", "/api/v1/financial/measures")).json()
        assert code not in {m["code"] for m in listed}
        everything = (
            await case.firm.request(
                "GET", "/api/v1/financial/measures", params={"include_deprecated": True}
            )
        ).json()
        assert next(m for m in everything if m["code"] == code)["deprecated_at"] is not None

    async def test_measures_list_qualifiers(self, case: Case) -> None:
        measures = {
            m["code"]: m
            for m in (await case.firm.request("GET", "/api/v1/financial/measures")).json()
        }
        assert measures["bonus"]["qualifier_required"] is True
        assert set(measures["bonus"]["qualifiers"]) == {
            "guaranteed",
            "discretionary",
            "performance",
            "other",
            "unspecified",
        }
        assert "overtime_premium" not in measures["allowance"]["qualifiers"]
        assert "premium" in measures["overtime"]["description"]
        assert measures["basic_pay"]["qualifiers"] == []


# --- 2. qualifiers ---------------------------------------------------------------------------


class TestQualifiers:
    @pytest.mark.parametrize(
        ("measure", "qualifier", "status"),
        [
            ("bonus", None, 422),
            ("bonus", "made_up", 422),
            ("bonus", "performance", 201),
            ("allowance", "car", 201),
            ("allowance", "overtime_premium", 422),
            ("basic_pay", "car", 422),
            ("basic_pay", None, 201),
        ],
    )
    async def test_api_rules(
        self, case: Case, measure: str, qualifier: str | None, status: int
    ) -> None:
        source = await case.income()
        resp = await case.observe(
            income_source_id=source["id"],
            measure=measure,
            qualifier=qualifier,
            value=money(10),
            frequency="annual",
        )
        assert resp.status_code == status, resp.text
        if status == 201:
            assert resp.json()["qualifier"] == qualifier

    @pytest.mark.parametrize(
        ("measure", "required", "qualifier"),
        [
            ("bonus", "true", "NULL"),  # missing
            ("bonus", "true", "'made_up'"),  # not listed
            ("basic_pay", "false", "'car'"),  # not allowed on this measure
            ("bonus", "false", "NULL"),  # lying about the definition
        ],
    )
    async def test_database_rules(
        self,
        case: Case,
        app_conn: asyncpg.Connection,
        measure: str,
        required: str,
        qualifier: str,
    ) -> None:
        source = await case.income()
        with pytest.raises((asyncpg.CheckViolationError, asyncpg.ForeignKeyViolationError)):
            await app_conn.execute(
                "INSERT INTO financial_observations (id, tenant_id, case_id, income_source_id,"  # noqa: S608 - fixed parameters
                " measure, qualifier, qualifier_required, value_type, temporal_kind,"
                " money_minor, currency, frequency, source_kind, recorded_at) VALUES"
                f" (gen_random_uuid(), $1, $2, $3, $4, {qualifier}, {required}, 'money',"
                " 'flow', 1, 'GBP', 'annual', 'declared', now())",
                case.firm.tenant_id,
                uuid.UUID(case.id),
                uuid.UUID(source["id"]),
                measure,
            )

    async def test_deprecated_qualifier_is_refused_everywhere(
        self, case: Case, owner: asyncpg.Connection, app_conn: asyncpg.Connection
    ) -> None:
        qualifier = f"test_{uuid.uuid4().hex[:12]}"
        async with owner.transaction():  # added and deprecated at once: never offered
            await owner.execute(
                "INSERT INTO measure_qualifiers (measure_code, subject_kind, qualifier,"
                " description) VALUES ('bonus', 'income_source', $1, 'Test qualifier')",
                qualifier,
            )
            await owner.execute(
                "UPDATE measure_qualifiers SET deprecated_at = now() WHERE qualifier = $1",
                qualifier,
            )
        source = await case.income()
        resp = await case.observe(
            income_source_id=source["id"],
            measure="bonus",
            qualifier=qualifier,
            value=money(10),
            frequency="annual",
        )
        assert resp.status_code == 422
        assert "deprecated" in resp.json()["detail"]
        measures = (await case.firm.request("GET", "/api/v1/financial/measures")).json()
        assert qualifier not in next(m for m in measures if m["code"] == "bonus")["qualifiers"]
        with pytest.raises(asyncpg.IntegrityConstraintViolationError, match="deprecated"):
            await app_conn.execute(
                "INSERT INTO financial_observations (id, tenant_id, case_id, income_source_id,"
                " measure, qualifier, qualifier_required, value_type, temporal_kind, money_minor,"
                " currency, frequency, source_kind, recorded_at) VALUES (gen_random_uuid(), $1,"
                " $2, $3, 'bonus', $4, true, 'money', 'flow', 1, 'GBP', 'annual', 'declared',"
                " now())",
                case.firm.tenant_id,
                uuid.UUID(case.id),
                uuid.UUID(source["id"]),
                qualifier,
            )

    async def test_same_period_different_qualifiers_coexist(self, case: Case) -> None:
        source = await case.income()
        for qualifier in ("car", "shift"):
            resp = await case.observe(
                income_source_id=source["id"],
                measure="allowance",
                qualifier=qualifier,
                value=money(100),
                frequency="monthly",
            )
            assert resp.status_code == 201


# --- 3. applicability ------------------------------------------------------------------------


class TestApplicability:
    async def test_measure_must_suit_the_item_kind(self, case: Case) -> None:
        rental = await case.income("property_rental")
        bad = await case.observe(
            income_source_id=rental["id"], measure="basic_pay", value=money(1), frequency="monthly"
        )
        assert bad.status_code == 422
        good = await case.observe(
            income_source_id=rental["id"], measure="gross_rent", value=money(1), frequency="monthly"
        )
        assert good.status_code == 201
        card = await case.commitment("credit_card")
        term = await case.observe(
            commitment_id=card["id"],
            measure="remaining_term",
            value={"type": "duration", "months": 3},
        )
        assert term.status_code == 422

    async def test_other_kind_accepts_every_measure(self, case: Case) -> None:
        other = await case.income("other")
        resp = await case.observe(
            income_source_id=other["id"], measure="gross_rent", value=money(1), frequency="monthly"
        )
        assert resp.status_code == 201

    async def test_kind_change_cannot_orphan_active_values(self, case: Case) -> None:
        source = await case.income()
        await case.observe(
            income_source_id=source["id"], measure="basic_pay", value=money(1), frequency="monthly"
        )
        resp = await case.firm.request(
            "PATCH",
            f"{case.base}/income-sources/{source['id']}",
            if_match=1,
            json={"kind": "property_rental"},
        )
        assert resp.status_code == 409
        ok = await case.firm.request(
            "PATCH",
            f"{case.base}/income-sources/{source['id']}",
            if_match=1,
            json={"kind": "directorship"},
        )
        assert ok.status_code == 200

    async def test_commitment_kind_change_is_checked_too(self, case: Case) -> None:
        loan = await case.commitment("personal_loan")
        await case.observe(
            commitment_id=loan["id"],
            measure="remaining_term",
            value={"type": "duration", "months": 12},
        )
        resp = await case.firm.request(
            "PATCH",
            f"{case.base}/commitments/{loan['id']}",
            if_match=1,
            json={"kind": "credit_card"},
        )
        assert resp.status_code == 409


# --- 4. supersession -------------------------------------------------------------------------


class TestSupersession:
    async def test_chain_is_traceable_both_ways(self, case: Case) -> None:
        source = await case.income()
        values = []
        previous: str | None = None
        for amount in (100, 110, 120):
            resp = await case.observe(
                income_source_id=source["id"],
                measure="basic_pay",
                value=money(amount),
                frequency="monthly",
                supersedes_observation_id=previous,
            )
            assert resp.status_code == 201, resp.text
            previous = resp.json()["id"]
            values.append(resp.json())

        history = {o["id"]: o for o in await case.get("observations", include_retired=True)}
        a, b, c = (history[v["id"]] for v in values)
        assert (a["superseded_by_observation_id"], b["superseded_by_observation_id"]) == (
            b["id"],
            c["id"],
        )
        assert (b["supersedes_observation_id"], c["supersedes_observation_id"]) == (
            a["id"],
            b["id"],
        )
        assert c["superseded_by_observation_id"] is None
        assert [history[x]["value"]["amount_minor"] for x in (a["id"], b["id"], c["id"])] == [
            100,
            110,
            120,
        ]
        assert [o["id"] for o in await case.get("observations")] == [c["id"]]

    async def _base_value(self, case: Case) -> tuple[dict[str, Any], dict[str, Any]]:
        source = await case.income()
        value = (
            await case.observe(
                income_source_id=source["id"],
                measure="basic_pay",
                value=money(100),
                frequency="monthly",
            )
        ).json()
        return source, value

    _INSERT = (
        "INSERT INTO financial_observations (id, tenant_id, case_id, income_source_id, measure,"
        " qualifier_required, value_type, temporal_kind, money_minor, currency, frequency,"
        " source_kind, recorded_at, supersedes_observation_id) VALUES (gen_random_uuid(),"
        " $1, $2, $3, 'basic_pay', false, 'money', 'flow', 200, 'GBP', $4, 'declared', now(), $5)"
    )

    async def test_database_rejects_a_successor_of_an_active_value(
        self, case: Case, app_conn: asyncpg.Connection
    ) -> None:
        source, value = await self._base_value(case)
        with pytest.raises(asyncpg.CheckViolationError, match="retired as superseded"):
            await app_conn.execute(
                self._INSERT,
                case.firm.tenant_id,
                uuid.UUID(case.id),
                uuid.UUID(source["id"]),
                "monthly",
                uuid.UUID(value["id"]),
            )

    async def test_database_rejects_a_successor_with_a_different_key(
        self, case: Case, app_conn: asyncpg.Connection
    ) -> None:
        source, value = await self._base_value(case)

        async def mismatched() -> None:
            async with app_conn.transaction():
                await app_conn.execute(
                    "UPDATE financial_observations SET retired_at = now(),"
                    " retired_reason = 'superseded' WHERE id = $1",
                    uuid.UUID(value["id"]),
                )
                await app_conn.execute(
                    self._INSERT,
                    case.firm.tenant_id,
                    uuid.UUID(case.id),
                    uuid.UUID(source["id"]),
                    "annual",
                    uuid.UUID(value["id"]),
                )

        with pytest.raises(asyncpg.CheckViolationError, match="must match"):
            await mismatched()

    async def test_database_rejects_superseded_without_successor(
        self, case: Case, app_conn: asyncpg.Connection
    ) -> None:
        _, value = await self._base_value(case)
        with pytest.raises(asyncpg.CheckViolationError, match="without a successor"):
            await app_conn.execute(
                "UPDATE financial_observations SET retired_at = now(),"
                " retired_reason = 'superseded' WHERE id = $1",
                uuid.UUID(value["id"]),
            )

    async def test_concurrent_raw_corrections_never_fork(
        self, case: Case, racers: tuple[Racer, Racer]
    ) -> None:
        """No case lock: the predecessor's row lock and retire-once decide the race."""
        source, value = await self._base_value(case)
        first, second = racers
        retire = (
            "UPDATE financial_observations SET retired_at = now(), retired_reason = 'superseded'"
            " WHERE id = $1"
        )
        args = (case.firm.tenant_id, uuid.UUID(case.id), uuid.UUID(source["id"]), "monthly")
        await first.conn.execute(retire, uuid.UUID(value["id"]))
        loser = await _blocks(second.conn.execute(retire, uuid.UUID(value["id"])))
        await first.conn.execute(self._INSERT, *args, uuid.UUID(value["id"]))
        await first.tx.commit()
        with pytest.raises(asyncpg.IntegrityConstraintViolationError, match="already retired"):
            await loser
        chain = await case.get("observations", include_retired=True)
        successors = [o for o in chain if o["supersedes_observation_id"] == value["id"]]
        assert len(successors) == 1
        assert [o["id"] for o in chain if o["retired_at"] is None] == [successors[0]["id"]]

    async def test_concurrent_corrections_never_fork(self, case: Case) -> None:
        source, value = await self._base_value(case)
        results = await asyncio.gather(
            *(
                case.observe(
                    income_source_id=source["id"],
                    measure="basic_pay",
                    value=money(amount),
                    frequency="monthly",
                    supersedes_observation_id=value["id"],
                )
                for amount in (101, 102, 103)
            )
        )
        statuses = sorted(r.status_code for r in results)
        assert statuses == [201, 409, 409], [r.text for r in results]
        assert len(await case.get("observations")) == 1


# --- 5. current values -------------------------------------------------------------------------


class TestCurrentValue:
    async def test_second_current_value_is_409_with_the_existing_id(self, case: Case) -> None:
        source = await case.income()
        body = {
            "income_source_id": source["id"],
            "measure": "basic_pay",
            "value": money(100),
            "frequency": "annual",
        }
        first = (await case.observe(**body)).json()
        second = await case.observe(**{**body, "value": money(200)})
        assert second.status_code == 409
        assert second.json()["current_observation_id"] == first["id"]

    async def test_different_period_or_source_coexist(self, case: Case) -> None:
        source = await case.income()
        common = {"income_source_id": source["id"], "measure": "basic_pay", "value": money(1)}
        for extra in (
            {"frequency": "monthly", "period_start": "2026-09-01", "period_end": "2026-09-30"},
            {"frequency": "monthly", "period_start": "2026-10-01", "period_end": "2026-10-31"},
            {
                "frequency": "monthly",
                "source_kind": "broker_entry",
                "period_start": "2026-10-01",
                "period_end": "2026-10-31",
            },
        ):
            assert (await case.observe(**common, **extra)).status_code == 201

    async def test_database_enforces_uniqueness(
        self, case: Case, app_conn: asyncpg.Connection
    ) -> None:
        source = await case.income()
        insert = (
            "INSERT INTO financial_observations (id, tenant_id, case_id, income_source_id,"
            " measure, qualifier_required, value_type, temporal_kind, money_minor, currency,"
            " frequency, source_kind, recorded_at, retired_at, retired_reason) VALUES"
            " (gen_random_uuid(), $1, $2, $3, 'overtime', false, 'money', 'flow', 1, 'GBP',"
            " 'annual', 'declared', now(), $4, $5)"
        )
        args = (case.firm.tenant_id, uuid.UUID(case.id), uuid.UUID(source["id"]))
        await app_conn.execute(insert, *args, None, None)
        await app_conn.execute(insert, *args, _now(), "entered_in_error")  # retired: allowed
        with pytest.raises(asyncpg.UniqueViolationError):
            await app_conn.execute(insert, *args, None, None)

    async def test_concurrent_raw_duplicates_cannot_both_commit(
        self, case: Case, racers: tuple[Racer, Racer]
    ) -> None:
        """No case lock: the unique index makes the second insert wait, then fail."""
        source = await case.income()
        insert = (
            "INSERT INTO financial_observations (id, tenant_id, case_id, income_source_id,"
            " measure, qualifier_required, value_type, temporal_kind, money_minor, currency,"
            " frequency, source_kind, recorded_at) VALUES (gen_random_uuid(), $1, $2, $3,"
            " 'overtime', false, 'money', 'flow', $4, 'GBP', 'annual', 'declared', now())"
        )
        args = (case.firm.tenant_id, uuid.UUID(case.id), uuid.UUID(source["id"]))
        first, second = racers
        await first.conn.execute(insert, *args, 1)
        loser = await _blocks(second.conn.execute(insert, *args, 2))
        await first.tx.commit()
        with pytest.raises(asyncpg.UniqueViolationError):
            await loser
        assert len(await case.get("observations")) == 1

    async def test_concurrent_duplicates_give_one_201(self, case: Case) -> None:
        source = await case.income()
        results = await asyncio.gather(
            *(
                case.observe(
                    income_source_id=source["id"],
                    measure="commission",
                    value=money(i),
                    frequency="annual",
                )
                for i in range(4)
            )
        )
        assert sorted(r.status_code for r in results) == [201, 409, 409, 409]


def _now() -> Any:
    from datetime import UTC, datetime

    return datetime.now(UTC)


# --- 6. withdrawal ----------------------------------------------------------------------------


class TestWithdrawal:
    async def _withdraw(self, case: Case, path: str, version: int = 1) -> Any:
        return await case.post(
            f"{path}/withdrawal", {"reason": "entered_in_error"}, if_match=version
        )

    async def test_withdrawal_retires_values_and_is_audited_once(self, case: Case) -> None:
        source = await case.income()
        for measure in ("basic_pay", "overtime"):
            await case.observe(
                income_source_id=source["id"], measure=measure, value=money(1), frequency="monthly"
            )
        resp = await self._withdraw(case, f"income-sources/{source['id']}")
        assert resp.status_code == 200, resp.text
        assert resp.json()["withdrawal_reason"] == "entered_in_error"

        assert await case.get("observations") == []
        retired = await case.get("observations", include_retired=True)
        assert {o["retired_reason"] for o in retired} == {"item_withdrawn"}
        assert await case.get("income-sources") == []
        assert len(await case.get("income-sources", include_withdrawn=True)) == 1

        events = (await case.firm.request("GET", f"/api/v1/cases/{case.id}/events")).json()["items"]
        withdrawn = [e for e in events if e["action"] == "financial_item.withdrawn"]
        assert len(withdrawn) == 1
        assert len(withdrawn[0]["changes"]["retired_observation_ids"]) == 2

    async def test_withdrawn_item_is_frozen(self, case: Case, app_conn: asyncpg.Connection) -> None:
        card = await case.commitment()
        withdrawn = (await self._withdraw(case, f"commitments/{card['id']}")).json()
        edit = await case.firm.request(
            "PATCH",
            f"{case.base}/commitments/{card['id']}",
            if_match=withdrawn["version"],
            json={"status": "settled"},
        )
        assert edit.status_code == 409
        value = await case.observe(
            commitment_id=card["id"], measure="outstanding_balance", value=money(1)
        )
        assert value.status_code == 409
        again = await self._withdraw(case, f"commitments/{card['id']}", withdrawn["version"])
        assert again.status_code == 409
        assert withdrawn["parties"][0]["applicant_id"] == case.applicant["id"]

        with pytest.raises(asyncpg.IntegrityConstraintViolationError, match="withdrawn"):
            await app_conn.execute(
                "UPDATE commitments SET withdrawn_at = NULL, withdrawal_reason = NULL "
                "WHERE id = $1",
                uuid.UUID(card["id"]),
            )
        with pytest.raises(asyncpg.IntegrityConstraintViolationError, match="withdrawn"):
            await app_conn.execute(
                "INSERT INTO financial_observations (id, tenant_id, case_id, commitment_id,"
                " measure, qualifier_required, value_type, temporal_kind, money_minor,"
                " currency, as_of_date, source_kind, recorded_at) VALUES (gen_random_uuid(),"
                " $1, $2, $3, 'outstanding_balance', false, 'money', 'stock', 1, 'GBP', now(),"
                " 'declared', now())",
                case.firm.tenant_id,
                uuid.UUID(case.id),
                uuid.UUID(card["id"]),
            )

    async def test_database_refuses_withdrawal_that_leaves_active_values(
        self, case: Case, app_conn: asyncpg.Connection
    ) -> None:
        source = await case.income()
        await case.observe(
            income_source_id=source["id"], measure="basic_pay", value=money(1), frequency="monthly"
        )
        with pytest.raises(asyncpg.IntegrityConstraintViolationError, match="active values"):
            async with app_conn.transaction():  # checked at commit (deferred)
                await app_conn.execute(
                    "UPDATE income_sources SET withdrawn_at = now(),"
                    " withdrawal_reason = 'duplicate' WHERE id = $1",
                    uuid.UUID(source["id"]),
                )
        assert len(await case.get("income-sources")) == 1
        assert len(await case.get("observations")) == 1

    _VALUE = (
        "INSERT INTO financial_observations (id, tenant_id, case_id, commitment_id, measure,"
        " qualifier_required, value_type, temporal_kind, money_minor, currency, as_of_date,"
        " source_kind, recorded_at) VALUES (gen_random_uuid(), $1, $2, $3,"
        " 'outstanding_balance', false, 'money', 'stock', 1, 'GBP', now(), 'declared', now())"
    )
    _WITHDRAW = (
        "UPDATE commitments SET withdrawn_at = now(), withdrawal_reason = 'duplicate' WHERE id = $1"
    )
    _RETIRE_ALL = (
        "UPDATE financial_observations SET retired_at = now(), retired_reason = 'item_withdrawn'"
        " WHERE commitment_id = $1 AND retired_at IS NULL"
    )

    async def _assert_consistent(self, case: Case, item_id: str) -> None:
        """Whatever the outcome, never a withdrawn item with an active value."""
        items = await case.get("commitments", include_withdrawn=True)
        withdrawn = next(i for i in items if i["id"] == item_id)["withdrawn_at"] is not None
        active = [o for o in await case.get("observations") if o["commitment_id"] == item_id]
        assert not (withdrawn and active)

    async def test_insert_first_then_withdrawal_retires_the_new_value(
        self, case: Case, racers: tuple[Racer, Racer]
    ) -> None:
        """Order 1: the insert holds the item lock; a correct withdrawal waits for it, then
        sees and retires the new value."""
        card = await case.commitment()
        item = uuid.UUID(card["id"])
        writer, withdrawer = racers
        await writer.conn.execute(self._VALUE, case.firm.tenant_id, uuid.UUID(case.id), item)
        waiting = await _blocks(withdrawer.conn.execute(self._WITHDRAW, item))
        await writer.tx.commit()
        await waiting
        await withdrawer.conn.execute(self._RETIRE_ALL, item)
        await withdrawer.tx.commit()
        retired = await case.get("observations", include_retired=True)
        assert [o["retired_reason"] for o in retired] == ["item_withdrawn"]
        await self._assert_consistent(case, card["id"])

    async def test_insert_first_then_careless_withdrawal_cannot_commit(
        self, case: Case, racers: tuple[Racer, Racer]
    ) -> None:
        """Order 1, careless writer: it waits for the insert, ignores the new value, and is
        refused at commit."""
        card = await case.commitment()
        item = uuid.UUID(card["id"])
        writer, withdrawer = racers
        await writer.conn.execute(self._VALUE, case.firm.tenant_id, uuid.UUID(case.id), item)
        waiting = await _blocks(withdrawer.conn.execute(self._WITHDRAW, item))
        await writer.tx.commit()
        await waiting
        with pytest.raises(asyncpg.IntegrityConstraintViolationError, match="active values"):
            await withdrawer.tx.commit()
        assert len(await case.get("commitments")) == 1
        await self._assert_consistent(case, card["id"])

    async def test_value_racing_a_withdrawal_waits_then_fails(self, case: Case) -> None:
        """Without the service's case lock: the insert must wait for the withdrawal's row
        lock and then see the item as withdrawn."""
        card = await case.commitment()
        withdrawer = await _connect("meip_app", case.firm.tenant_id)
        writer = await _connect("meip_app", case.firm.tenant_id)
        try:
            tx = withdrawer.transaction()
            await tx.start()
            await withdrawer.execute(
                "UPDATE commitments SET withdrawn_at = now(), withdrawal_reason = 'duplicate'"
                " WHERE id = $1",
                uuid.UUID(card["id"]),
            )
            insert = asyncio.create_task(
                writer.execute(
                    "INSERT INTO financial_observations (id, tenant_id, case_id, commitment_id,"
                    " measure, qualifier_required, value_type, temporal_kind, money_minor,"
                    " currency, as_of_date, source_kind, recorded_at) VALUES"
                    " (gen_random_uuid(), $1, $2, $3, 'outstanding_balance', false, 'money',"
                    " 'stock', 1, 'GBP', now(), 'declared', now())",
                    case.firm.tenant_id,
                    uuid.UUID(case.id),
                    uuid.UUID(card["id"]),
                )
            )
            await asyncio.sleep(0.5)
            assert not insert.done(), "the insert did not wait for the withdrawal"
            await tx.commit()
            with pytest.raises(asyncpg.IntegrityConstraintViolationError, match="withdrawn"):
                await insert
        finally:
            await withdrawer.close()
            await writer.close()
        assert await case.get("observations", include_retired=True) == []
        await self._assert_consistent(case, card["id"])

    async def test_withdrawn_parties_are_frozen(
        self, case: Case, app_conn: asyncpg.Connection
    ) -> None:
        card = await case.commitment()
        spend = await case.post(
            "expenditure",
            {
                "category": "transport",
                "scope": "individual",
                "parties": [{"applicant_id": case.applicant["id"], "share": "100"}],
            },
        )
        assert spend.status_code == 201, spend.text
        await self._withdraw(case, f"commitments/{card['id']}")
        await self._withdraw(case, f"expenditure/{spend.json()['id']}")
        applicant = uuid.UUID(case.applicant["id"])
        for statement, item in (
            ("DELETE FROM commitment_parties WHERE commitment_id = $1", card["id"]),
            ("DELETE FROM expenditure_parties WHERE expenditure_item_id = $1", spend.json()["id"]),
        ):
            with pytest.raises(asyncpg.IntegrityConstraintViolationError, match="withdrawn"):
                await app_conn.execute(statement, uuid.UUID(item))
        with pytest.raises(asyncpg.IntegrityConstraintViolationError, match="withdrawn"):
            await app_conn.execute(
                "INSERT INTO commitment_parties (commitment_id, applicant_id, tenant_id, case_id,"
                " liability_role, created_at) VALUES ($1, $2, $3, $4, 'principal', now())",
                uuid.UUID(card["id"]),
                applicant,
                case.firm.tenant_id,
                uuid.UUID(case.id),
            )
        listed = await case.get("commitments", include_withdrawn=True)
        assert [p["applicant_id"] for p in listed[0]["parties"]] == [case.applicant["id"]]

    async def test_withdrawal_is_atomic(self, case: Case, monkeypatch: pytest.MonkeyPatch) -> None:
        source = await case.income()
        await case.observe(
            income_source_id=source["id"], measure="basic_pay", value=money(1), frequency="monthly"
        )

        async def failing_record(*args: Any, **kwargs: Any) -> None:
            raise RuntimeError("audit store unavailable")

        monkeypatch.setattr(audit_service, "record", failing_record)
        resp = await self._withdraw(case, f"income-sources/{source['id']}")
        monkeypatch.undo()

        assert resp.status_code == 500
        assert len(await case.get("income-sources")) == 1
        active = await case.get("observations")
        assert len(active) == 1
        assert active[0]["retired_at"] is None


# --- 7. audit details are bound to their event's transaction --------------------------------


class TestAuditDetailBinding:
    async def _old_event_id(self, case: Case) -> uuid.UUID:
        events = (await case.firm.request("GET", f"/api/v1/cases/{case.id}/events")).json()["items"]
        return uuid.UUID(events[0]["id"])

    async def test_details_for_an_earlier_event_are_rejected(
        self, case: Case, app_conn: asyncpg.Connection
    ) -> None:
        event_id = await self._old_event_id(case)
        with pytest.raises(asyncpg.InsufficientPrivilegeError, match="same transaction"):
            await app_conn.execute(
                "INSERT INTO audit_event_details (event_id, tenant_id, details) "
                "VALUES ($1, $2, '{}')",
                event_id,
                case.firm.tenant_id,
            )

    async def test_copy_cannot_bypass_the_binding(
        self, case: Case, app_conn: asyncpg.Connection
    ) -> None:
        """PostgreSQL refuses COPY FROM outright on row-level-secured tables for a role
        without BYPASSRLS, so COPY is not a write path at all for the runtime role."""
        event_id = await self._old_event_id(case)
        with pytest.raises(asyncpg.FeatureNotSupportedError, match="row-level security"):
            await app_conn.copy_records_to_table(
                "audit_event_details",
                records=[(event_id, case.firm.tenant_id, "{}")],
                columns=["event_id", "tenant_id", "details"],
            )

    async def test_runtime_role_cannot_set_txid(
        self, case: Case, app_conn: asyncpg.Connection
    ) -> None:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await app_conn.execute(
                "INSERT INTO audit_events (id, tenant_id, occurred_at, actor_type, actor_ref,"
                " action, entity_type, entity_id, changes, txid) VALUES (gen_random_uuid(), $1,"
                " now(), 'system', 'x', 'x', 'x', gen_random_uuid(), '{}', '1'::xid8)",
                case.firm.tenant_id,
            )

    async def test_same_transaction_works_even_inside_a_savepoint(
        self, case: Case, app_conn: asyncpg.Connection
    ) -> None:
        event_id = uuid.uuid4()
        async with app_conn.transaction():
            await app_conn.execute(
                "INSERT INTO audit_events (id, tenant_id, occurred_at, actor_type, actor_ref,"
                " action, entity_type, entity_id, changes) VALUES ($1, $2, now(), 'system',"
                " 'test', 'test.event', 'test', gen_random_uuid(), '{}')",
                event_id,
                case.firm.tenant_id,
            )
            async with app_conn.transaction():  # a savepoint
                await app_conn.execute(
                    "INSERT INTO audit_event_details (event_id, tenant_id, details) "
                    'VALUES ($1, $2, \'{"note": "ok"}\')',
                    event_id,
                    case.firm.tenant_id,
                )
        assert (
            await app_conn.fetchval(
                "SELECT count(*) FROM audit_event_details WHERE event_id = $1", event_id
            )
            == 1
        )

    async def test_every_route_still_writes_its_details(self, case: Case) -> None:
        source = await case.income()
        await case.observe(
            income_source_id=source["id"],
            measure="bonus",
            qualifier="guaranteed",
            value=money(5),
            frequency="annual",
        )
        events = (await case.firm.request("GET", f"/api/v1/cases/{case.id}/events")).json()["items"]
        assert events[-1]["details"]["money_minor"] == {"from": None, "to": 5}
