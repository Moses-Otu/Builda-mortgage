"""Error handlers: unexpected failures must not leak internals."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.orm.exc import StaleDataError

from meip.app import create_app
from meip.core.config import Settings


@pytest.fixture
async def failing_client(app: FastAPI, settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    # A separate app instance, so the shared app's route table and OpenAPI stay untouched.
    broken = create_app(
        settings, auth_provider=app.state.auth_provider, sessionmaker=app.state.sessionmaker
    )

    @broken.get("/boom")
    async def boom() -> None:
        raise RuntimeError("secret internal detail: connection string xyz")

    @broken.get("/stale")
    async def stale() -> None:
        raise StaleDataError("UPDATE statement on table 'cases' expected to update 1 row")

    transport = httpx.ASGITransport(app=broken, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def test_unhandled_error_is_generic_500(failing_client: httpx.AsyncClient) -> None:
    resp = await failing_client.get("/boom")
    assert resp.status_code == 500
    assert resp.headers["content-type"] == "application/problem+json"
    assert resp.json()["type"].endswith(":internal-error")
    assert "secret" not in resp.text
    assert resp.json()["request_id"] == resp.headers["x-request-id"]


async def test_stale_data_maps_to_version_conflict(failing_client: httpx.AsyncClient) -> None:
    resp = await failing_client.get("/stale")
    assert resp.status_code == 409
    assert resp.json()["type"].endswith(":version-conflict")
    assert "cases" not in resp.text
