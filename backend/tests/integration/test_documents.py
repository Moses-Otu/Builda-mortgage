"""Step 2-A: document upload, storage and verified download.

Every document used here is a tiny, clearly labelled SYNTHETIC PDF (``synthetic_pdf``).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import asyncpg
import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from fastapi import FastAPI
from structlog.testing import capture_logs

from meip.app import create_app
from meip.auth.provider import JWTAuthProvider, StaticKeyResolver
from meip.core.config import Settings
from tests.conftest import AUDIENCE, ISSUER, TEST_DB, Firm, db_url, synthetic_pdf

MAX = 3 * 1024 * 1024  # the test settings' document_max_bytes


@pytest.fixture
async def case(firm: Firm) -> dict[str, Any]:
    created = await firm.create_case()
    created["applicant"] = await firm.add_applicant(created["id"])
    return created


def _docs(case: dict[str, Any]) -> str:
    return f"/api/v1/cases/{case['id']}/documents"


async def _connect(role: str, tenant_id: uuid.UUID | None = None) -> asyncpg.Connection:
    conn = await asyncpg.connect(db_url(role, TEST_DB, driver="postgresql"))
    if tenant_id is not None:
        await conn.execute("SELECT set_config('app.tenant_id', $1, false)", str(tenant_id))
    return conn


@pytest.fixture
async def owner() -> AsyncIterator[asyncpg.Connection]:
    conn = await _connect("meip_owner")
    try:
        yield conn
    finally:
        await conn.close()


async def _stored_path(owner: asyncpg.Connection, blob_root: Path, document_id: str) -> Path:
    key = await owner.fetchval(
        "SELECT blob_key FROM document_versions WHERE document_id = $1", uuid.UUID(document_id)
    )
    return blob_root / str(key)


# --- upload and download -----------------------------------------------------------------


class TestRoundTrip:
    @pytest.mark.parametrize("size", [600, 1_500_000], ids=["in-memory", "spooled-to-disk"])
    async def test_download_is_byte_identical_and_verified(
        self, firm: Firm, case: dict[str, Any], size: int
    ) -> None:
        content = synthetic_pdf(f"round-trip {uuid.uuid4()}")
        content += b"%" + b"x" * (size - len(content) - 1)
        assert len(content) == size
        resp = await firm.upload(case["id"], content, applicant_id=case["applicant"]["id"])
        assert resp.status_code == 201, resp.text
        out = resp.json()
        sha = hashlib.sha256(content).hexdigest()
        assert out["original"]["sha256"] == sha
        assert out["original"]["size_bytes"] == size
        assert out["original"]["scan_status"] == "not_scanned"
        assert out["applicant_id"] == case["applicant"]["id"]
        assert out["status"] == "received"
        assert "blob_key" not in str(out)

        download = await firm.request("GET", f"{_docs(case)}/{out['id']}/content")
        assert download.status_code == 200
        assert download.content == content
        assert download.headers["content-type"] == "application/pdf"
        assert download.headers["x-content-type-options"] == "nosniff"
        assert download.headers["cache-control"] == "no-store"
        assert download.headers["content-disposition"] == (
            f'attachment; filename="document-{out["id"]}.pdf"'
        )
        digest = base64.b64encode(hashlib.sha256(content).digest()).decode()
        assert download.headers["repr-digest"] == f"sha-256=:{digest}:"

    async def test_list_and_get(self, firm: Firm, case: dict[str, Any]) -> None:
        first = (await firm.upload(case["id"])).json()
        second = (await firm.upload(case["id"])).json()
        listed = (await firm.request("GET", _docs(case))).json()
        assert [d["id"] for d in listed] == [first["id"], second["id"]]
        got = await firm.request("GET", f"{_docs(case)}/{first['id']}")
        assert got.json() == first
        assert got.headers["etag"] == '"1"'
        missing = await firm.request("GET", f"{_docs(case)}/{uuid.uuid4()}")
        assert missing.status_code == 404

    async def test_file_name_is_sanitised_and_kept_out_of_headers(
        self, firm: Firm, case: dict[str, Any]
    ) -> None:
        resp = await firm.upload(case["id"], filename="..%2F..%2Fetc%2Fpay%0Aslip%E2%80%AE.pdf")
        assert resp.status_code == 201, resp.text
        out = resp.json()
        assert out["original"]["original_filename"] == "payslip.pdf"
        download = await firm.request("GET", f"{_docs(case)}/{out['id']}/content")
        assert "payslip" not in download.headers["content-disposition"]


# --- validation --------------------------------------------------------------------------


async def _chunked(content: bytes, chunk: int = 64 * 1024) -> AsyncIterator[bytes]:
    for start in range(0, len(content), chunk):
        yield content[start : start + chunk]


class TestValidation:
    async def test_declared_type_must_be_pdf(self, firm: Firm, case: dict[str, Any]) -> None:
        resp = await firm.upload(case["id"], content_type="image/png")
        assert resp.status_code == 415
        assert resp.json()["type"] == "urn:meip:problem:unsupported-media-type"

    @pytest.mark.parametrize(
        "content",
        [b"GIF89a....", b" %PDF-1.4 leading space", b"%PDF"],
        ids=["gif", "offset", "short"],
    )
    async def test_signature_must_be_at_byte_zero(
        self, firm: Firm, case: dict[str, Any], content: bytes
    ) -> None:
        resp = await firm.upload(case["id"], content)
        assert resp.status_code == 415

    async def test_empty_body(self, firm: Firm, case: dict[str, Any]) -> None:
        resp = await firm.upload(case["id"], b"")
        assert resp.status_code == 422

    async def test_declared_size_over_the_limit(self, firm: Firm, case: dict[str, Any]) -> None:
        resp = await firm.upload(case["id"], b"%PDF-" + b"x" * MAX)
        assert resp.status_code == 413

    async def test_size_is_enforced_while_streaming_without_content_length(
        self, firm: Firm, case: dict[str, Any]
    ) -> None:
        body = b"%PDF-" + b"x" * MAX  # one byte over, sent chunked with no Content-Length
        resp = await firm.request(
            "POST",
            _docs(case),
            params={"document_type": "payslip"},
            content=_chunked(body),
            headers={"Content-Type": "application/pdf"},
        )
        assert resp.status_code == 413
        assert (await firm.request("GET", _docs(case))).json() == []

    async def test_size_exactly_at_the_limit_is_accepted(
        self, firm: Firm, case: dict[str, Any]
    ) -> None:
        body = synthetic_pdf("limit")
        body += b"x" * (MAX - len(body))
        resp = await firm.request(
            "POST",
            _docs(case),
            params={"document_type": "payslip"},
            content=_chunked(body),
            headers={"Content-Type": "application/pdf"},
        )
        assert resp.status_code == 201, resp.text

    async def test_document_type_is_required_and_limited(
        self, firm: Firm, case: dict[str, Any]
    ) -> None:
        # bank_statement is a valid type since Stage 2-B (local and test only).
        for params in ({}, {"document_type": "p60"}, {"document_type": "credit_report"}):
            resp = await firm.request(
                "POST",
                _docs(case),
                params=params,
                content=synthetic_pdf(),
                headers={"Content-Type": "application/pdf"},
            )
            assert resp.status_code == 422

    async def test_applicant_must_be_active_and_on_the_case(
        self, firm: Firm, case: dict[str, Any]
    ) -> None:
        other = await firm.create_case()
        stranger = await firm.add_applicant(other["id"])
        resp = await firm.upload(case["id"], applicant_id=stranger["id"])
        assert resp.status_code == 422

        leaving = await firm.add_applicant(case["id"])
        removal = await firm.request(
            "POST",
            f"/api/v1/cases/{case['id']}/applicants/{leaving['id']}/removal",
            if_match=1,
            json={"removal_reason": "added_in_error"},
        )
        assert removal.status_code == 200, removal.text
        resp = await firm.upload(case["id"], applicant_id=leaving["id"])
        assert resp.status_code == 409

    async def test_closed_case_refuses_uploads(self, firm: Firm) -> None:
        closed = await firm.create_case()
        await firm.add_applicant(closed["id"])
        closing = await firm.request(
            "POST",
            f"/api/v1/cases/{closed['id']}/transitions",
            if_match=1,
            json={"to_status": "closed", "closure_reason": "not_proceeding"},
        )
        assert closing.status_code == 200, closing.text
        resp = await firm.upload(closed["id"])
        assert resp.status_code == 409


# --- duplicates (P2-5) -------------------------------------------------------------------


def _blob_count(blob_root: Path, firm: Firm) -> int:
    tenant_dir = blob_root / str(firm.tenant_id)
    return sum(1 for _ in tenant_dir.rglob("original")) if tenant_dir.exists() else 0


class TestDuplicates:
    async def test_same_content_in_a_case_is_409_with_the_existing_id(
        self, firm: Firm, case: dict[str, Any], blob_root: Path
    ) -> None:
        content = synthetic_pdf("dup")
        first = await firm.upload(case["id"], content)
        assert first.status_code == 201
        stored = _blob_count(blob_root, firm)
        again = await firm.upload(case["id"], content)
        assert again.status_code == 409
        assert again.json()["type"] == "urn:meip:problem:duplicate-document"
        assert again.json()["existing_document_id"] == first.json()["id"]
        assert _blob_count(blob_root, firm) == stored  # nothing stored for the duplicate

        elsewhere = await firm.create_case()
        assert (await firm.upload(elsewhere["id"], content)).status_code == 201

    async def test_concurrent_identical_uploads_store_one(
        self, firm: Firm, case: dict[str, Any], blob_root: Path
    ) -> None:
        content = synthetic_pdf("concurrent")
        results = await asyncio.gather(*(firm.upload(case["id"], content) for _ in range(4)))
        assert sorted(r.status_code for r in results) == [201, 409, 409, 409]
        assert len((await firm.request("GET", _docs(case))).json()) == 1
        assert _blob_count(blob_root, firm) == 1

    async def test_database_refuses_duplicates_from_concurrent_raw_writers(
        self, firm: Firm, case: dict[str, Any]
    ) -> None:
        """Without the service's case lock: the unique index makes the second writer wait
        for the first, then fail."""
        sha = hashlib.sha256(uuid.uuid4().bytes).hexdigest()
        writers = [await _connect("meip_app", firm.tenant_id) for _ in range(2)]
        try:
            transactions = [w.transaction() for w in writers]
            for tx in transactions:
                await tx.start()
            statements = []
            for writer in writers:
                document_id, version_id = uuid.uuid4(), uuid.uuid4()
                await writer.execute(
                    "INSERT INTO documents (id, tenant_id, case_id, document_type, status,"
                    " created_at, updated_at, version) VALUES ($1, $2, $3, 'payslip',"
                    " 'received', now(), now(), 1)",
                    document_id,
                    firm.tenant_id,
                    uuid.UUID(case["id"]),
                )
                key = f"{firm.tenant_id}/{case['id']}/{document_id}/{version_id}/original"
                statements.append(
                    (
                        "INSERT INTO document_versions (id, tenant_id, case_id, document_id,"
                        " version_number, sha256, size_bytes, media_type, blob_key,"
                        " scan_status, uploaded_at) VALUES ($1, $2, $3, $4, 1, $5, 10,"
                        " 'application/pdf', $6, 'not_scanned', now())",
                        version_id,
                        firm.tenant_id,
                        uuid.UUID(case["id"]),
                        document_id,
                        sha,
                        key,
                    )
                )
            await writers[0].execute(*statements[0])
            loser = asyncio.create_task(writers[1].execute(*statements[1]))
            await asyncio.sleep(0.5)
            assert not loser.done(), "the second insert should wait for the first"
            await transactions[0].commit()
            with pytest.raises(asyncpg.UniqueViolationError):
                await loser
        finally:
            for writer in writers:
                await writer.close()
        assert len((await firm.request("GET", _docs(case))).json()) == 1


# --- integrity, immutability and privacy --------------------------------------------------


class TestIntegrity:
    async def test_tampered_original_is_never_served(
        self, firm: Firm, case: dict[str, Any], owner: asyncpg.Connection, blob_root: Path
    ) -> None:
        out = (await firm.upload(case["id"], filename="secret-name.pdf")).json()
        path = await _stored_path(owner, blob_root, out["id"])
        path.write_bytes(path.read_bytes() + b"tampered")
        with capture_logs() as logs:
            resp = await firm.request("GET", f"{_docs(case)}/{out['id']}/content")
        assert resp.status_code == 500
        assert resp.json()["type"] == "urn:meip:problem:integrity-error"
        assert b"SYNTHETIC" not in resp.content
        failures = [e for e in logs if e["event"] == "document_integrity_failure"]
        assert failures == [
            {
                "event": "document_integrity_failure",
                "document_id": out["id"],
                "reason": "hash_mismatch",
                "log_level": "error",
            }
        ]

    async def test_missing_original_is_an_integrity_error(
        self, firm: Firm, case: dict[str, Any], owner: asyncpg.Connection, blob_root: Path
    ) -> None:
        out = (await firm.upload(case["id"])).json()
        (await _stored_path(owner, blob_root, out["id"])).unlink()
        resp = await firm.request("GET", f"{_docs(case)}/{out['id']}/content")
        assert resp.status_code == 500
        assert resp.json()["type"] == "urn:meip:problem:integrity-error"

    async def test_file_name_and_hash_never_reach_the_logs(
        self, firm: Firm, case: dict[str, Any]
    ) -> None:
        content = synthetic_pdf("log-check")
        with capture_logs() as logs:
            resp = await firm.upload(case["id"], content, filename="Jane-Doe-payslip.pdf")
            assert resp.status_code == 201
            await firm.request("GET", f"{_docs(case)}/{resp.json()['id']}/content")
            await firm.upload(case["id"], content, filename="Jane-Doe-payslip.pdf")  # 409
        text = repr(logs)
        assert "Jane-Doe" not in text
        assert hashlib.sha256(content).hexdigest() not in text

    async def test_upload_is_audited_once_with_erasable_name_and_hash(
        self, firm: Firm, case: dict[str, Any]
    ) -> None:
        content = synthetic_pdf("audit")
        out = (await firm.upload(case["id"], content, filename="audit.pdf")).json()
        events = (await firm.request("GET", f"/api/v1/cases/{case['id']}/events")).json()["items"]
        uploads = [e for e in events if e["action"] == "document.uploaded"]
        assert len(uploads) == 1
        event = uploads[0]
        assert event["entity_id"] == out["id"]
        assert event["changes"]["document_version_id"] == out["original"]["id"]
        assert event["changes"]["original_filename"] == {"changed": True}
        assert event["changes"]["sha256"] == {"changed": True}
        assert event["details"]["original_filename"] == "audit.pdf"
        assert event["details"]["sha256"] == hashlib.sha256(content).hexdigest()


class TestDatabaseRules:
    @pytest.mark.parametrize(
        "statement",
        [
            "UPDATE document_versions SET size_bytes = 1",
            "DELETE FROM document_versions",
            "DELETE FROM documents",
            "TRUNCATE document_versions",
        ],
    )
    async def test_runtime_role_cannot_change_or_delete(
        self, firm: Firm, case: dict[str, Any], statement: str
    ) -> None:
        await firm.upload(case["id"])
        conn = await _connect("meip_app", firm.tenant_id)
        try:
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.execute(statement)
        finally:
            await conn.close()

    @pytest.mark.parametrize(
        "statement",
        [
            "UPDATE document_versions SET original_filename = 'x' WHERE document_id = $1",
            "DELETE FROM document_versions WHERE document_id = $1",
        ],
    )
    async def test_originals_are_immutable_even_for_the_owner(
        self, firm: Firm, case: dict[str, Any], owner: asyncpg.Connection, statement: str
    ) -> None:
        out = (await firm.upload(case["id"])).json()
        with pytest.raises(asyncpg.IntegrityConstraintViolationError, match="immutable"):
            await owner.execute(statement, uuid.UUID(out["id"]))

    async def test_owner_cannot_truncate_originals(self, owner: asyncpg.Connection) -> None:
        with pytest.raises(asyncpg.IntegrityConstraintViolationError, match="immutable"):
            async with owner.transaction():  # rolled back either way
                await owner.execute("TRUNCATE document_versions CASCADE")

    @pytest.mark.parametrize(
        ("column", "value"),
        [
            ("sha256", "A" * 64),
            ("blob_key", "../../etc/passwd"),
            ("media_type", "text/html"),
            ("size_bytes", 0),
        ],
    )
    async def test_stored_metadata_is_constrained(
        self, firm: Firm, case: dict[str, Any], column: str, value: object
    ) -> None:
        conn = await _connect("meip_app", firm.tenant_id)
        document_id, version_id = uuid.uuid4(), uuid.uuid4()
        row: dict[str, object] = {
            "sha256": hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
            "blob_key": f"{firm.tenant_id}/{case['id']}/{document_id}/{version_id}/original",
            "media_type": "application/pdf",
            "size_bytes": 10,
        }
        row[column] = value
        try:
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO documents (id, tenant_id, case_id, document_type, status,"
                    " created_at, updated_at, version) VALUES ($1, $2, $3, 'payslip',"
                    " 'received', now(), now(), 1)",
                    document_id,
                    firm.tenant_id,
                    uuid.UUID(case["id"]),
                )
                with pytest.raises(asyncpg.CheckViolationError):
                    await conn.execute(
                        "INSERT INTO document_versions (id, tenant_id, case_id, document_id,"
                        " version_number, sha256, size_bytes, media_type, blob_key,"
                        " scan_status, uploaded_at) VALUES ($1, $2, $3, $4, 1, $5, $6, $7, $8,"
                        " 'not_scanned', now())",
                        version_id,
                        firm.tenant_id,
                        uuid.UUID(case["id"]),
                        document_id,
                        row["sha256"],
                        row["size_bytes"],
                        row["media_type"],
                        row["blob_key"],
                    )
                raise _RollbackError
        except _RollbackError:
            pass
        finally:
            await conn.close()


class _RollbackError(Exception):
    pass


# --- fail-closed gating (P2-8) ------------------------------------------------------------


async def test_documents_disabled_means_404(
    firm: Firm, case: dict[str, Any], settings: Settings, signing_key: RSAPrivateKey
) -> None:
    disabled: FastAPI = create_app(
        settings.model_copy(update={"documents_enabled": False}),
        auth_provider=JWTAuthProvider(
            issuer=ISSUER,
            audience=AUDIENCE,
            key_resolver=StaticKeyResolver(signing_key.public_key()),
        ),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=disabled), base_url="http://test"
    ) as client:
        headers = {**firm.headers(), "Content-Type": "application/pdf"}
        for method, path in (
            ("POST", _docs(case)),
            ("GET", _docs(case)),
            ("GET", f"{_docs(case)}/{uuid.uuid4()}"),
            ("GET", f"{_docs(case)}/{uuid.uuid4()}/content"),
        ):
            resp = await client.request(
                method,
                path,
                headers=headers,
                params={"document_type": "payslip"},
                content=synthetic_pdf() if method == "POST" else None,
            )
            assert resp.status_code == 404, (method, path)
            assert resp.json()["type"] == "urn:meip:problem:feature-disabled"
    assert (await firm.request("GET", _docs(case))).json() == []
