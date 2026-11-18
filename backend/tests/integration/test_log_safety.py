"""Sensitive values must never reach logs, whatever path an error takes."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.exc import IntegrityError

from meip.app import create_app
from meip.core.config import Settings
from meip.core.logging import configure_logging
from tests.conftest import Firm

SECRET_NAME = "Sensitive Borrowername"
SECRET_EMAIL = "borrower.private@example.test"
SECRET_AMOUNT = "98765432"
SECRETS = (SECRET_NAME, SECRET_EMAIL, SECRET_AMOUNT)


class _FakeDriverError(Exception):
    """Shaped like asyncpg's error: SQLSTATE plus a message embedding row values."""

    sqlstate = "23505"
    constraint_name = "uq_memberships_tenant_id_user_id"


@pytest.fixture
async def failing_client(app: FastAPI, settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    broken = create_app(
        settings, auth_provider=app.state.auth_provider, sessionmaker=app.state.sessionmaker
    )

    @broken.get("/explode")
    async def explode() -> None:
        try:
            raise ValueError(f"lookup failed for {SECRET_EMAIL}")
        except ValueError as cause:
            raise RuntimeError(f"cannot process {SECRET_NAME} income {SECRET_AMOUNT}") from cause

    @broken.get("/duplicate")
    async def duplicate() -> None:
        driver_error = _FakeDriverError(
            f"duplicate key value violates unique constraint; Key (email)=({SECRET_EMAIL})"
        )
        raise IntegrityError("INSERT INTO memberships ...", {"email": SECRET_EMAIL}, driver_error)

    @broken.get("/fk")
    async def foreign_key() -> None:
        driver_error = _FakeDriverError(f"Key (case_id)=({SECRET_AMOUNT}) is not present")
        driver_error.sqlstate = "23503"
        raise IntegrityError("INSERT INTO applicants ...", {"x": SECRET_AMOUNT}, driver_error)

    transport = httpx.ASGITransport(app=broken, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
async def info_firm(app: FastAPI, settings: Settings, firm: Firm) -> AsyncIterator[Firm]:
    """The firm, served by an app instance logging at INFO so request lines are emitted."""
    verbose = create_app(
        settings.model_copy(update={"log_level": "INFO"}),
        auth_provider=app.state.auth_provider,
        sessionmaker=app.state.sessionmaker,
    )
    transport = httpx.ASGITransport(app=verbose)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield Firm(
            tenant_id=firm.tenant_id,
            admin_subject=firm.admin_subject,
            admin_membership_id=firm.admin_membership_id,
            key=firm.key,
            client=client,
        )
    configure_logging(settings.log_level, settings.log_json)  # restore the suite's level


def _log_lines(captured: str) -> list[dict[str, object]]:
    lines = []
    for raw in captured.splitlines():
        try:
            lines.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return lines


def _assert_clean(text: str) -> None:
    for secret in SECRETS:
        assert secret not in text, f"sensitive value {secret!r} leaked"


async def test_unhandled_exception_logs_type_and_frames_only(
    failing_client: httpx.AsyncClient, capsys: pytest.CaptureFixture[str]
) -> None:
    resp = await failing_client.get("/explode")
    assert resp.status_code == 500
    out = capsys.readouterr().out
    _assert_clean(out)
    _assert_clean(resp.text)

    error = next(e for e in _log_lines(out) if e.get("event") == "unhandled_error")
    frames = error["exception"]
    assert isinstance(frames, list)
    assert "builtins.RuntimeError" in frames
    assert "builtins.ValueError" in frames  # the cause chain is kept, without messages
    assert any("explode" in str(f) for f in frames)
    assert error["request_id"] == resp.headers["x-request-id"]


async def test_unique_violation_is_409_and_logged_without_values(
    failing_client: httpx.AsyncClient, capsys: pytest.CaptureFixture[str]
) -> None:
    resp = await failing_client.get("/duplicate")
    assert resp.status_code == 409
    assert resp.json()["type"] == "urn:meip:problem:conflict"
    out = capsys.readouterr().out
    _assert_clean(out)
    _assert_clean(resp.text)
    event = next(e for e in _log_lines(out) if e.get("event") == "integrity_violation")
    assert event["sqlstate"] == "23505"
    assert event["constraint"] == "uq_memberships_tenant_id_user_id"


async def test_other_integrity_violation_is_422_without_values(
    failing_client: httpx.AsyncClient, capsys: pytest.CaptureFixture[str]
) -> None:
    resp = await failing_client.get("/fk")
    assert resp.status_code == 422
    assert resp.json()["type"] == "urn:meip:problem:constraint-violation"
    _assert_clean(capsys.readouterr().out)
    _assert_clean(resp.text)


async def test_unmatched_path_is_not_logged(
    info_firm: Firm, capsys: pytest.CaptureFixture[str]
) -> None:
    resp = await info_firm.client.get(
        f"/api/v1/lookup/{SECRET_NAME.replace(' ', '%20')}/{SECRET_EMAIL}"
    )
    assert resp.status_code == 404
    out = capsys.readouterr().out
    _assert_clean(out)
    request_log = next(e for e in _log_lines(out) if e.get("event") == "request")
    assert request_log["route"] == "<unmatched>"


async def test_validation_and_business_errors_do_not_log_or_echo_values(
    firm: Firm, capsys: pytest.CaptureFixture[str]
) -> None:
    case = await firm.create_case()
    invalid = await firm.request(
        "POST",
        f"/api/v1/cases/{case['id']}/applicants",
        json={"first_name": SECRET_NAME, "last_name": "", "middle_names": SECRET_EMAIL},
    )
    assert invalid.status_code == 422
    rule = await firm.request(
        "POST",
        "/api/v1/cases",
        json={
            "purpose": "purchase",
            "property_value": {"amount_minor": 1, "currency": "GBP"},
            "loan_amount": {"amount_minor": int(SECRET_AMOUNT), "currency": "GBP"},
        },
    )
    # Loan above value is advisory (a warning), and the warning must not echo the amount.
    assert rule.status_code == 201
    assert rule.json()["warnings"][0]["code"] == "loan_exceeds_property_value"
    out = capsys.readouterr().out
    _assert_clean(out)
    _assert_clean(invalid.text)
    # The created resource legitimately returns the caller's own amount; the warning must not.
    _assert_clean(str(rule.json()["warnings"]))


async def test_successful_requests_log_route_templates_not_values(
    info_firm: Firm, capsys: pytest.CaptureFixture[str]
) -> None:
    case = await info_firm.create_case()
    applicant = await info_firm.add_applicant(case["id"], first_name=SECRET_NAME)
    source = await info_firm.request(
        "POST",
        f"/api/v1/cases/{case['id']}/financial/income-sources",
        json={"applicant_id": applicant["id"], "kind": "employment", "name": SECRET_NAME},
    )
    await info_firm.request(
        "POST",
        f"/api/v1/cases/{case['id']}/financial/observations",
        json={
            "income_source_id": source.json()["id"],
            "measure": "basic_pay",
            "value": {"type": "money", "amount_minor": int(SECRET_AMOUNT)},
            "frequency": "annual",
        },
    )
    out = capsys.readouterr().out
    _assert_clean(out)
    routes = {e.get("route") for e in _log_lines(out) if e.get("event") == "request"}
    assert "/api/v1/cases/{case_id}/applicants" in routes
