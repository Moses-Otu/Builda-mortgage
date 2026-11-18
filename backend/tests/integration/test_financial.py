"""Typed financial items and observations (Phase 1.1-E)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import asyncpg
import pytest

from tests.conftest import TEST_DB, Firm, MakeFirm, db_url


class Case:
    """A case with two active borrowers, and helpers for its financial endpoints."""

    def __init__(self, firm: Firm, case: dict[str, Any], a: dict[str, Any], b: dict[str, Any]):
        self.firm, self.case, self.a, self.b = firm, case, a, b
        self.id = case["id"]
        self.base = f"/api/v1/cases/{self.id}/financial"

    async def post(self, path: str, body: dict[str, Any]) -> Any:
        return await self.firm.request("POST", f"{self.base}/{path}", json=body)

    async def get(self, path: str, **params: Any) -> Any:
        return (await self.firm.request("GET", f"{self.base}/{path}", params=params)).json()

    async def income(self, applicant: dict[str, Any] | None = None, **body: Any) -> dict[str, Any]:
        applicant = applicant or self.a
        resp = await self.post(
            "income-sources", {"applicant_id": applicant["id"], "kind": "employment", **body}
        )
        assert resp.status_code == 201, resp.text
        out: dict[str, Any] = resp.json()
        return out

    async def commitment(self, *applicants: dict[str, Any], **body: Any) -> dict[str, Any]:
        parties = [{"applicant_id": a["id"]} for a in (applicants or (self.a,))]
        resp = await self.post("commitments", {"kind": "credit_card", "parties": parties, **body})
        assert resp.status_code == 201, resp.text
        out: dict[str, Any] = resp.json()
        return out

    async def observe(self, **body: Any) -> Any:
        return await self.post("observations", body)


@pytest.fixture
async def case(firm: Firm) -> Case:
    created = await firm.create_case()
    a = await firm.add_applicant(created["id"], first_name="Ann")
    b = await firm.add_applicant(created["id"], first_name="Ben")
    return Case(firm, created, a, b)


def money(minor: int, kind: str = "money") -> dict[str, Any]:
    return {"type": kind, "amount_minor": minor, "currency": "GBP"}


class TestTypedValues:
    async def test_measures_reference_lists_types(self, case: Case) -> None:
        measures = (await case.firm.request("GET", "/api/v1/financial/measures")).json()
        index = {(m["code"], m["subject_kind"]): m for m in measures}
        assert index[("basic_pay", "income_source")]["value_type"] == "money"
        assert index[("interest_rate", "commitment")]["value_type"] == "rate"
        assert index[("remaining_term", "commitment")]["temporal_kind"] == "stock"

    async def test_each_value_type_round_trips(self, case: Case) -> None:
        source = await case.income()
        business = await case.income(kind="self_employment")
        loan = await case.commitment(kind="personal_loan")
        cases = [
            {
                "income_source_id": source["id"],
                "measure": "basic_pay",
                "value": money(250_000),
                "frequency": "monthly",
                "period_start": "2026-10-01",
                "period_end": "2026-10-31",
            },
            {
                "income_source_id": business["id"],
                "measure": "net_profit",
                "value": money(-120_000, "signed_money"),
                "frequency": "annual",
                "tax_year": "2025-26",
            },
            {
                "income_source_id": source["id"],
                "measure": "pension_contribution_rate",
                "value": {"type": "proportion", "percent": "5.5"},
            },
            {
                "commitment_id": loan["id"],
                "measure": "interest_rate",
                "value": {"type": "rate", "percent": "29.9"},
            },
            {
                "commitment_id": loan["id"],
                "measure": "remaining_term",
                "value": {"type": "duration", "months": 18},
                "as_of_date": "2026-11-01",
            },
            {
                "commitment_id": loan["id"],
                "measure": "payments_remaining",
                "value": {"type": "count", "count": 18},
                "as_of_date": "2026-11-01",
            },
            {
                "commitment_id": loan["id"],
                "measure": "final_payment_date",
                "value": {"type": "date", "date": "2028-05-01"},
            },
        ]
        for body in cases:
            resp = await case.observe(**body)
            assert resp.status_code == 201, (body["measure"], resp.text)
            assert resp.json()["value"] == {
                **body["value"],
                **({"currency": "GBP"} if body["value"]["type"].endswith("money") else {}),
                **(
                    {"percent": f"{float(body['value']['percent']):.4f}"}
                    if "percent" in body["value"]
                    else {}
                ),
            }

    @pytest.mark.parametrize(
        ("measure", "value", "extra"),
        [
            ("basic_pay", {"type": "rate", "percent": "5"}, {"frequency": "monthly"}),
            ("basic_pay", {"type": "duration", "months": 3}, {"frequency": "monthly"}),
            ("pension_contribution_rate", money(5), {}),
        ],
    )
    async def test_value_must_match_the_measure_type(
        self, case: Case, measure: str, value: dict[str, Any], extra: dict[str, Any]
    ) -> None:
        source = await case.income()
        resp = await case.observe(
            income_source_id=source["id"], measure=measure, value=value, **extra
        )
        assert resp.status_code == 422
        assert "takes a" in resp.json()["detail"]

    async def test_negative_money_only_for_signed_measures(self, case: Case) -> None:
        source = await case.income()
        bad = await case.observe(
            income_source_id=source["id"], measure="basic_pay", value=money(-1), frequency="monthly"
        )
        assert bad.status_code == 422

    async def test_proportion_cannot_exceed_100(self, case: Case) -> None:
        source = await case.income()
        resp = await case.observe(
            income_source_id=source["id"],
            measure="pension_contribution_rate",
            value={"type": "proportion", "percent": "100.5"},
        )
        assert resp.status_code == 422

    async def test_measure_must_belong_to_the_subject_kind(self, case: Case) -> None:
        loan = await case.commitment()
        resp = await case.observe(
            commitment_id=loan["id"], measure="basic_pay", value=money(1), frequency="monthly"
        )
        assert resp.status_code == 422
        assert "not a measure for a commitment" in resp.json()["detail"]

    async def test_exactly_one_subject(self, case: Case) -> None:
        source = await case.income()
        loan = await case.commitment()
        resp = await case.observe(
            income_source_id=source["id"],
            commitment_id=loan["id"],
            measure="basic_pay",
            value=money(1),
            frequency="monthly",
        )
        assert resp.status_code == 422


class TestTemporalRules:
    async def test_flow_needs_a_frequency(self, case: Case) -> None:
        source = await case.income()
        resp = await case.observe(income_source_id=source["id"], measure="bonus", value=money(1))
        assert resp.status_code == 422

    async def test_stock_defaults_its_as_of_date(self, case: Case) -> None:
        loan = await case.commitment()
        resp = await case.observe(
            commitment_id=loan["id"], measure="outstanding_balance", value=money(80_000)
        )
        assert resp.status_code == 201
        assert resp.json()["as_of_date"] is not None
        assert resp.json()["frequency"] is None

    async def test_stock_rejects_a_frequency(self, case: Case) -> None:
        loan = await case.commitment()
        resp = await case.observe(
            commitment_id=loan["id"],
            measure="outstanding_balance",
            value=money(1),
            frequency="monthly",
        )
        assert resp.status_code == 422

    async def test_attribute_rejects_periods(self, case: Case) -> None:
        loan = await case.commitment()
        resp = await case.observe(
            commitment_id=loan["id"],
            measure="interest_rate",
            value={"type": "rate", "percent": "3"},
            period_start="2026-01-01",
        )
        assert resp.status_code == 422

    @pytest.mark.parametrize("tax_year", ["2025", "25-26", "2025/26"])
    async def test_tax_year_format(self, case: Case, tax_year: str) -> None:
        source = await case.income()
        resp = await case.observe(
            income_source_id=source["id"],
            measure="net_profit",
            value=money(1, "signed_money"),
            frequency="annual",
            tax_year=tax_year,
        )
        assert resp.status_code == 422


class TestCorrections:
    async def test_supersede_retires_the_old_value(self, case: Case) -> None:
        source = await case.income()
        first = (
            await case.observe(
                income_source_id=source["id"],
                measure="basic_pay",
                value=money(100),
                frequency="monthly",
            )
        ).json()
        second = await case.observe(
            income_source_id=source["id"],
            measure="basic_pay",
            value=money(200),
            frequency="monthly",
            supersedes_observation_id=first["id"],
        )
        assert second.status_code == 201
        current = await case.get("observations")
        assert [o["value"]["amount_minor"] for o in current] == [200]
        history = await case.get("observations", include_retired=True)
        old = next(o for o in history if o["id"] == first["id"])
        assert old["retired_reason"] == "superseded"
        assert old["value"]["amount_minor"] == 100  # never edited

    async def test_correction_must_be_same_item_and_measure(self, case: Case) -> None:
        source = await case.income()
        first = (
            await case.observe(
                income_source_id=source["id"],
                measure="basic_pay",
                value=money(100),
                frequency="monthly",
            )
        ).json()
        resp = await case.observe(
            income_source_id=source["id"],
            measure="overtime",
            value=money(5),
            frequency="monthly",
            supersedes_observation_id=first["id"],
        )
        assert resp.status_code == 422

    async def test_retirement_is_final(self, case: Case) -> None:
        loan = await case.commitment()
        obs = (
            await case.observe(
                commitment_id=loan["id"], measure="outstanding_balance", value=money(5)
            )
        ).json()
        path = f"observations/{obs['id']}/retirement"
        first = await case.post(path, {"reason": "entered_in_error"})
        assert first.status_code == 200
        assert (await case.post(path, {"reason": "entered_in_error"})).status_code == 409
        superseding = await case.observe(
            commitment_id=loan["id"],
            measure="outstanding_balance",
            value=money(6),
            supersedes_observation_id=obs["id"],
        )
        assert superseding.status_code == 409

    async def test_value_detail_is_erasable_in_audit(self, case: Case) -> None:
        source = await case.income()
        await case.observe(
            income_source_id=source["id"],
            measure="bonus",
            qualifier="discretionary",
            value=money(777_777),
            frequency="annual",
        )
        events = (await case.firm.request("GET", f"/api/v1/cases/{case.id}/events")).json()["items"]
        recorded = events[-1]
        assert recorded["action"] == "observation.recorded"
        assert recorded["changes"]["money_minor"] == {"changed": True}
        assert recorded["changes"]["measure"] == {"from": None, "to": "bonus"}
        assert recorded["details"]["money_minor"] == {"from": None, "to": 777_777}


class TestLiabilityAndApplicantRemoval:
    async def test_joint_commitment_with_allocation(self, case: Case) -> None:
        resp = await case.post(
            "commitments",
            {
                "kind": "personal_loan",
                "parties": [
                    {
                        "applicant_id": case.a["id"],
                        "responsibility_share": "70",
                        "share_basis": "declared",
                    },
                    {
                        "applicant_id": case.b["id"],
                        "responsibility_share": "30",
                        "share_basis": "declared",
                    },
                ],
            },
        )
        assert resp.status_code == 201, resp.text
        parties = {p["applicant_id"]: p for p in resp.json()["parties"]}
        assert parties[case.a["id"]]["liability_role"] == "principal"
        assert parties[case.b["id"]]["responsibility_share"] == "30.0000"

    async def test_share_and_basis_go_together(self, case: Case) -> None:
        resp = await case.post(
            "commitments",
            {
                "kind": "other",
                "parties": [{"applicant_id": case.a["id"], "responsibility_share": "50"}],
            },
        )
        assert resp.status_code == 422

    async def test_commitment_needs_a_liable_party(self, case: Case) -> None:
        assert (await case.post("commitments", {"kind": "other", "parties": []})).status_code == 422

    async def test_removal_does_not_erase_or_reassign_obligations(self, case: Case) -> None:
        sole = await case.commitment(case.b, kind="car_finance")
        joint = await case.commitment(case.a, case.b, kind="personal_loan")
        b_income = await case.income(case.b)

        removal = await case.firm.request(
            "POST",
            f"/api/v1/cases/{case.id}/applicants/{case.b['id']}/removal",
            if_match=case.b["version"],
            json={"removal_reason": "no_longer_applying"},
        )
        assert removal.status_code == 200, removal.text

        commitments = {c["id"]: c for c in await case.get("commitments")}
        assert [p["applicant_id"] for p in commitments[sole["id"]]["parties"]] == [case.b["id"]]
        assert {p["applicant_id"] for p in commitments[joint["id"]]["parties"]} == {
            case.a["id"],
            case.b["id"],
        }
        incomes = await case.get("income-sources")
        assert b_income["id"] in {i["id"] for i in incomes}

    async def test_removed_applicant_cannot_gain_new_records(self, case: Case) -> None:
        b_income = await case.income(case.b)
        await case.firm.request(
            "POST",
            f"/api/v1/cases/{case.id}/applicants/{case.b['id']}/removal",
            if_match=case.b["version"],
            json={"removal_reason": "no_longer_applying"},
        )
        new_income = await case.post(
            "income-sources", {"applicant_id": case.b["id"], "kind": "pension"}
        )
        assert new_income.status_code == 409
        new_party = await case.post(
            "commitments", {"kind": "other", "parties": [{"applicant_id": case.b["id"]}]}
        )
        assert new_party.status_code == 409
        new_value = await case.observe(
            income_source_id=b_income["id"],
            measure="basic_pay",
            value=money(1),
            frequency="monthly",
        )
        assert new_value.status_code == 409

    async def test_existing_removed_party_may_stay_when_parties_are_edited(
        self, case: Case
    ) -> None:
        joint = await case.commitment(case.a, case.b)
        await case.firm.request(
            "POST",
            f"/api/v1/cases/{case.id}/applicants/{case.b['id']}/removal",
            if_match=case.b["version"],
            json={"removal_reason": "no_longer_applying"},
        )
        resp = await case.firm.request(
            "PATCH",
            f"{case.base}/commitments/{joint['id']}",
            if_match=1,
            json={
                "parties": [
                    {"applicant_id": case.a["id"]},
                    {"applicant_id": case.b["id"], "liability_role": "guarantor"},
                ]
            },
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["version"] == 2
        roles = {p["applicant_id"]: p["liability_role"] for p in resp.json()["parties"]}
        assert roles == {case.a["id"]: "principal", case.b["id"]: "guarantor"}

    async def test_applicant_from_another_case_rejected(self, case: Case, firm: Firm) -> None:
        other = await firm.create_case()
        stranger = await firm.add_applicant(other["id"])
        resp = await case.post("income-sources", {"applicant_id": stranger["id"], "kind": "other"})
        assert resp.status_code == 422


class TestExpenditure:
    async def test_household_has_no_parties_individual_needs_some(self, case: Case) -> None:
        household = await case.post(
            "expenditure", {"category": "council_tax", "scope": "household"}
        )
        assert household.status_code == 201
        assert household.json()["parties"] == []
        bad_household = await case.post(
            "expenditure",
            {
                "category": "utilities",
                "scope": "household",
                "parties": [{"applicant_id": case.a["id"]}],
            },
        )
        assert bad_household.status_code == 422
        bad_individual = await case.post(
            "expenditure", {"category": "childcare", "scope": "individual"}
        )
        assert bad_individual.status_code == 422
        individual = await case.post(
            "expenditure",
            {
                "category": "transport",
                "scope": "individual",
                "parties": [{"applicant_id": case.a["id"], "share": "100"}],
            },
        )
        assert individual.status_code == 201

    async def test_scope_and_parties_change_together(self, case: Case) -> None:
        item = (
            await case.post("expenditure", {"category": "transport", "scope": "household"})
        ).json()
        alone = await case.firm.request(
            "PATCH",
            f"{case.base}/expenditure/{item['id']}",
            if_match=1,
            json={"scope": "individual"},
        )
        assert alone.status_code == 422
        both = await case.firm.request(
            "PATCH",
            f"{case.base}/expenditure/{item['id']}",
            if_match=1,
            json={"scope": "individual", "parties": [{"applicant_id": case.b["id"]}]},
        )
        assert both.status_code == 200, both.text
        assert [p["applicant_id"] for p in both.json()["parties"]] == [case.b["id"]]


class TestEditability:
    async def test_closed_case_blocks_financial_writes(self, case: Case) -> None:
        await case.firm.request(
            "POST",
            f"/api/v1/cases/{case.id}/transitions",
            if_match=1,
            json={"to_status": "closed", "closure_reason": "not_proceeding"},
        )
        resp = await case.post("income-sources", {"applicant_id": case.a["id"], "kind": "other"})
        assert resp.status_code == 409


# --- database-level guarantees, bypassing the API ------------------------------------------


@pytest.fixture
async def app_conn(case: Case) -> AsyncIterator[asyncpg.Connection]:
    conn = await asyncpg.connect(db_url("meip_app", TEST_DB, driver="postgresql"))
    await conn.execute("SELECT set_config('app.tenant_id', $1, false)", str(case.firm.tenant_id))
    try:
        yield conn
    finally:
        await conn.close()


class TestDatabaseGuarantees:
    async def test_values_are_immutable_for_the_runtime_role(
        self, case: Case, app_conn: asyncpg.Connection
    ) -> None:
        loan = await case.commitment()
        obs = (
            await case.observe(
                commitment_id=loan["id"], measure="outstanding_balance", value=money(10)
            )
        ).json()
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await app_conn.execute(
                "UPDATE financial_observations SET money_minor = 1 WHERE id = $1",
                uuid.UUID(obs["id"]),
            )
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await app_conn.execute(
                "DELETE FROM financial_observations WHERE id = $1", uuid.UUID(obs["id"])
            )

    async def test_retirement_cannot_be_undone(
        self, case: Case, app_conn: asyncpg.Connection
    ) -> None:
        loan = await case.commitment()
        obs = (
            await case.observe(
                commitment_id=loan["id"], measure="outstanding_balance", value=money(10)
            )
        ).json()
        await case.post(f"observations/{obs['id']}/retirement", {"reason": "entered_in_error"})
        with pytest.raises(asyncpg.IntegrityConstraintViolationError, match="already retired"):
            await app_conn.execute(
                "UPDATE financial_observations SET retired_at = NULL, retired_reason = NULL "
                "WHERE id = $1",
                uuid.UUID(obs["id"]),
            )

    @pytest.mark.parametrize(
        ("value_type", "columns"),
        [
            ("money", "percent = 5"),  # wrong column for the type
            ("rate", "money_minor = 5, currency = 'GBP'"),
        ],
    )
    async def test_measure_definition_is_enforced_by_the_database(
        self, case: Case, app_conn: asyncpg.Connection, value_type: str, columns: str
    ) -> None:
        loan = await case.commitment()
        sql_columns = ", ".join(c.split(" = ")[0] for c in columns.split(", "))
        sql_values = ", ".join(c.split(" = ")[1] for c in columns.split(", "))
        with pytest.raises((asyncpg.CheckViolationError, asyncpg.ForeignKeyViolationError)):
            await app_conn.execute(
                "INSERT INTO financial_observations (id, tenant_id, case_id, commitment_id,"  # noqa: S608 - fixed test parameters
                " measure, qualifier_required, value_type, temporal_kind,"
                f" {sql_columns}, source_kind, recorded_at)"
                " VALUES (gen_random_uuid(), $1, $2, $3, 'interest_rate', false, $4,"
                f" 'attribute', {sql_values}, 'declared', now())",
                case.firm.tenant_id,
                uuid.UUID(case.id),
                uuid.UUID(loan["id"]),
                value_type,
            )

    async def test_observation_cannot_point_at_another_cases_item(
        self, case: Case, app_conn: asyncpg.Connection, firm: Firm
    ) -> None:
        loan = await case.commitment()
        other = await firm.create_case()
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await app_conn.execute(
                "INSERT INTO financial_observations (id, tenant_id, case_id, commitment_id,"
                " measure, qualifier_required, value_type, temporal_kind, money_minor,"
                " currency, as_of_date, source_kind, recorded_at) VALUES (gen_random_uuid(),"
                " $1, $2, $3, 'outstanding_balance', false, 'money', 'stock', 1, 'GBP', now(),"
                " 'declared', now())",
                case.firm.tenant_id,
                uuid.UUID(other["id"]),
                uuid.UUID(loan["id"]),
            )

    async def test_party_cannot_be_an_applicant_from_another_case(
        self, case: Case, firm: Firm, make_firm: MakeFirm
    ) -> None:
        loan = await case.commitment()
        other = await firm.create_case()
        stranger = await firm.add_applicant(other["id"])
        conn = await asyncpg.connect(db_url("meip_owner", TEST_DB, driver="postgresql"))
        try:
            with pytest.raises(asyncpg.ForeignKeyViolationError):
                await conn.execute(
                    "INSERT INTO commitment_parties (commitment_id, applicant_id, tenant_id,"
                    " case_id, liability_role, created_at)"
                    " VALUES ($1, $2, $3, $4, 'principal', now())",
                    uuid.UUID(loan["id"]),
                    uuid.UUID(stranger["id"]),
                    firm.tenant_id,
                    uuid.UUID(case.id),
                )
        finally:
            await conn.close()


class TestReviewFixes:
    """Regression tests for the Phase 1.1 pre-commit review (D1, D2, D4, D5)."""

    async def test_d1_employer_name_is_erasable_not_permanent(self, case: Case) -> None:
        await case.income(name="Identifying Employer Ltd")
        events = (await case.firm.request("GET", f"/api/v1/cases/{case.id}/events")).json()["items"]
        added = events[-1]
        assert added["changes"]["name"] == {"changed": True}
        assert "Identifying Employer Ltd" not in str(added["changes"])
        assert added["details"]["name"] == {"from": None, "to": "Identifying Employer Ltd"}

    async def test_d2_correction_cannot_move_a_value_between_sources(self, case: Case) -> None:
        source = await case.income()
        declared = (
            await case.observe(
                income_source_id=source["id"],
                measure="basic_pay",
                value=money(100),
                frequency="monthly",
                source_kind="declared",
            )
        ).json()
        resp = await case.observe(
            income_source_id=source["id"],
            measure="basic_pay",
            value=money(200),
            frequency="monthly",
            source_kind="broker_entry",
            supersedes_observation_id=declared["id"],
        )
        assert resp.status_code == 422
        active = await case.get("observations")
        assert [o["source_kind"] for o in active] == ["declared"]

    async def test_d2_correction_must_cover_the_same_period(self, case: Case) -> None:
        source = await case.income()
        october = (
            await case.observe(
                income_source_id=source["id"],
                measure="basic_pay",
                value=money(100),
                frequency="monthly",
                period_start="2026-10-01",
                period_end="2026-10-31",
            )
        ).json()
        november = await case.observe(
            income_source_id=source["id"],
            measure="basic_pay",
            value=money(110),
            frequency="monthly",
            period_start="2026-11-01",
            period_end="2026-11-30",
            supersedes_observation_id=october["id"],
        )
        assert november.status_code == 422
        other_frequency = await case.observe(
            income_source_id=source["id"],
            measure="basic_pay",
            value=money(1200),
            frequency="annual",
            period_start="2026-10-01",
            period_end="2026-10-31",
            supersedes_observation_id=october["id"],
        )
        assert other_frequency.status_code == 422

    async def test_d2_stock_correction_keeps_the_original_as_of_date(self, case: Case) -> None:
        loan = await case.commitment()
        original = (
            await case.observe(
                commitment_id=loan["id"],
                measure="outstanding_balance",
                value=money(5000),
                as_of_date="2026-09-30",
            )
        ).json()
        corrected = await case.observe(
            commitment_id=loan["id"],
            measure="outstanding_balance",
            value=money(5100),
            supersedes_observation_id=original["id"],
        )
        assert corrected.status_code == 201, corrected.text
        assert corrected.json()["as_of_date"] == "2026-09-30"

    async def test_d4_removed_applicants_income_source_is_read_only(self, case: Case) -> None:
        b_income = await case.income(case.b)
        await case.firm.request(
            "POST",
            f"/api/v1/cases/{case.id}/applicants/{case.b['id']}/removal",
            if_match=case.b["version"],
            json={"removal_reason": "no_longer_applying"},
        )
        resp = await case.firm.request(
            "PATCH",
            f"{case.base}/income-sources/{b_income['id']}",
            if_match=1,
            json={"name": "New employer"},
        )
        assert resp.status_code == 409

    async def test_d5_database_allows_only_one_correction_per_value(
        self, case: Case, app_conn: asyncpg.Connection
    ) -> None:
        source = await case.income()
        first = (
            await case.observe(
                income_source_id=source["id"],
                measure="bonus",
                qualifier="guaranteed",
                value=money(1),
                frequency="annual",
            )
        ).json()
        insert = (
            "INSERT INTO financial_observations (id, tenant_id, case_id, income_source_id,"
            " measure, qualifier, qualifier_required, value_type, temporal_kind, money_minor,"
            " currency, frequency, source_kind, recorded_at, supersedes_observation_id)"
            " VALUES (gen_random_uuid(), $1, $2, $3, 'bonus', 'guaranteed', true, 'money',"
            " 'flow', $4, 'GBP', 'annual', 'declared', now(), $5)"
        )
        args = (case.firm.tenant_id, uuid.UUID(case.id), uuid.UUID(source["id"]))

        async def two_successors() -> None:
            async with app_conn.transaction():
                await app_conn.execute(
                    "UPDATE financial_observations SET retired_at = now(),"
                    " retired_reason = 'superseded' WHERE id = $1",
                    uuid.UUID(first["id"]),
                )
                await app_conn.execute(insert, *args, 2, uuid.UUID(first["id"]))
                await app_conn.execute(insert, *args, 3, uuid.UUID(first["id"]))

        with pytest.raises(asyncpg.UniqueViolationError):
            await two_successors()
