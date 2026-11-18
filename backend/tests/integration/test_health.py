from __future__ import annotations

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import create_async_engine

from meip.app import create_app
from meip.core.config import Settings
from meip.core.db import create_sessionmaker


async def test_health(client: httpx.AsyncClient) -> None:
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_ready_checks_database(client: httpx.AsyncClient) -> None:
    resp = await client.get("/ready")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_ready_reports_unavailable_database(app: FastAPI, settings: Settings) -> None:
    dead = create_async_engine("postgresql+asyncpg://nobody:x@127.0.0.1:1/none")
    broken = create_app(
        settings, auth_provider=app.state.auth_provider, sessionmaker=create_sessionmaker(dead)
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=broken), base_url="http://test"
    ) as c:
        resp = await c.get("/ready")
    await dead.dispose()
    assert resp.status_code == 503
    assert resp.json() == {"status": "unavailable"}


async def test_request_id_generated_and_echoed(client: httpx.AsyncClient) -> None:
    generated = await client.get("/health")
    assert len(generated.headers["x-request-id"]) == 36

    echoed = await client.get("/health", headers={"X-Request-ID": "trace-abc.123"})
    assert echoed.headers["x-request-id"] == "trace-abc.123"


async def test_malformed_request_id_is_replaced(client: httpx.AsyncClient) -> None:
    resp = await client.get("/health", headers={"X-Request-ID": "bad id with spaces <script>"})
    assert resp.headers["x-request-id"] != "bad id with spaces <script>"
    assert len(resp.headers["x-request-id"]) == 36
