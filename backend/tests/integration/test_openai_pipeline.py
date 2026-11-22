"""Phase 4: the worker pipeline with the OpenAI provider (API mocked; SYNTHETIC documents).

A success is stored as an ``openai`` run with the deterministic fields; an outage leaves the
document to be retried, never failed on the first attempt."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import asyncpg
import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from meip.core.config import Settings
from meip.core.db import create_engine, create_sessionmaker
from meip.documents import processing
from meip.documents.openai_provider import OpenAILayoutProvider
from meip.documents.processing import run_once
from meip.documents.storage import FilesystemBlobStore
from tests.conftest import TEST_DB, Firm, db_url
from tests.synthetic_docs import payslip_series
from tests.unit.test_openai_provider import echo_segments

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


def _use(monkeypatch: pytest.MonkeyPatch, settings: Settings, handler: Any) -> None:
    keyed = settings.model_copy(update={"openai_api_key": SecretStr("sk-test-not-a-real-key")})
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(
        processing,
        "build_layout_provider",
        lambda _s: OpenAILayoutProvider(keyed, client=client, backoff=0),
    )


async def test_a_payslip_is_extracted_through_openai(
    firm: Firm, worker: Worker, owner: asyncpg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use(monkeypatch, worker[2], echo_segments)
    case = await firm.create_case()
    applicant = await firm.add_applicant(case["id"])
    upload = await firm.upload(
        case["id"], payslip_series(months=1)[0].document.pdf(), applicant_id=applicant["id"]
    )
    assert upload.status_code == 201, upload.text
    while await run_once(*worker):
        pass
    doc = (
        await firm.request("GET", f"/api/v1/cases/{case['id']}/documents/{upload.json()['id']}")
    ).json()
    assert doc["status"] == "extracted"
    x = (
        await firm.request(
            "GET", f"/api/v1/cases/{case['id']}/documents/{upload.json()['id']}/extraction"
        )
    ).json()
    assert x["provider"] == "openai"
    codes = {f["field_code"] for f in x["summary"]}
    assert {"pay_date", "gross_pay", "net_pay"} <= codes
    raw = await owner.fetchval(
        "SELECT raw_output::text FROM extraction_runs r JOIN document_versions v "
        "ON v.id = r.document_version_id WHERE v.document_id = $1",
        upload.json()["id"],
    )
    assert '"source": "openai"' in raw
    assert "base64" not in raw


async def test_an_outage_leaves_the_document_to_be_retried(
    firm: Firm, worker: Worker, owner: asyncpg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use(monkeypatch, worker[2], lambda _r: httpx.Response(503, json={}))
    case = await firm.create_case()
    applicant = await firm.add_applicant(case["id"])
    upload = await firm.upload(
        case["id"], payslip_series(months=1)[0].document.pdf(), applicant_id=applicant["id"]
    )
    for _ in range(4):
        if not await run_once(*worker):
            break
    doc = (
        await firm.request("GET", f"/api/v1/cases/{case['id']}/documents/{upload.json()['id']}")
    ).json()
    assert doc["status"] == "ready"  # not failed: the extraction job waits to be retried
    job = await owner.fetchrow(
        "SELECT j.status, j.attempts, j.error_code FROM jobs j "
        "JOIN document_versions v ON v.id = j.document_version_id "
        "WHERE v.document_id = $1 AND j.kind = 'document.extract'",
        upload.json()["id"],
    )
    assert job is not None
    assert job["status"] == "queued"  # waiting for its retry, one minute later
    assert job["attempts"] == 1
    assert job["error_code"] == "transient"  # the worker records codes, never provider details
