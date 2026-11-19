"""Shared test fixtures.

Integration tests run against a real PostgreSQL server (row-level security cannot be
tested against SQLite or mocks). Point ``MEIP_TEST_ADMIN_DATABASE_URL`` at a superuser
connection; the suite creates the roles and a disposable ``meip_test`` database.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import asyncpg
import httpx
import pytest
from alembic import command
from alembic.config import Config
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from fastapi import FastAPI
from sqlalchemy import Connection, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from meip.app import create_app
from meip.audit.models import AuditEvent
from meip.auth.dev import generate_key, mint_token
from meip.auth.provider import JWTAuthProvider, StaticKeyResolver
from meip.core.config import AuthMode, BlobBackend, Environment, Settings
from meip.core.db import create_engine, create_sessionmaker
from meip.tenancy.service import onboard_tenant

BACKEND_DIR = Path(__file__).resolve().parents[1]
ADMIN_URL = os.environ.get(
    "MEIP_TEST_ADMIN_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/postgres"
)
TEST_DB = "meip_test"
ISSUER = "https://meip-test.localhost/"
AUDIENCE = "meip-api"
ROLE_PASSWORDS = {"meip_owner": "meip_owner_local", "meip_app": "meip_app_local"}


def synthetic_pdf(label: str = "") -> bytes:
    """A tiny, clearly labelled SYNTHETIC PDF: never real borrower data. Unique per label
    (pass a fresh label for distinct content)."""
    marker = f"SYNTHETIC TEST DOCUMENT {label or uuid.uuid4()}".encode()
    return b"\n".join(
        [b"%PDF-1.4", b"% " + marker, b"1 0 obj << /Type /Catalog >> endobj", b"%%EOF", b""]
    )


def db_url(role: str, database: str, driver: str = "postgresql+asyncpg") -> str:
    url = make_url(ADMIN_URL).set(
        drivername=driver, username=role, password=ROLE_PASSWORDS[role], database=database
    )
    return url.render_as_string(hide_password=False)


async def create_database(name: str) -> None:
    conn = await asyncpg.connect(ADMIN_URL)
    try:
        await conn.execute((BACKEND_DIR / "scripts/db/roles.sql").read_text())
        await conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await conn.execute(f'CREATE DATABASE "{name}" OWNER meip_owner')
        await conn.execute(f'REVOKE ALL ON DATABASE "{name}" FROM PUBLIC')
        await conn.execute(f'GRANT CONNECT ON DATABASE "{name}" TO meip_app')
    finally:
        await conn.close()


def alembic_config(connection: Connection) -> Config:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.attributes["connection"] = connection
    return cfg


async def run_alembic(engine: AsyncEngine, action: str, revision: str) -> None:
    def _run(connection: Connection) -> None:
        getattr(command, action)(alembic_config(connection), revision)

    async with engine.begin() as conn:
        await conn.run_sync(_run)


# --- Database & app ---------------------------------------------------------------------


@pytest.fixture(scope="session")
async def owner_engine() -> AsyncIterator[AsyncEngine]:
    await create_database(TEST_DB)
    engine = create_engine(db_url("meip_owner", TEST_DB), pool_size=2)
    await run_alembic(engine, "upgrade", "head")
    yield engine
    await engine.dispose()


@pytest.fixture(scope="session")
def owner_sessionmaker(owner_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return create_sessionmaker(owner_engine)


@pytest.fixture(scope="session")
def signing_key() -> RSAPrivateKey:
    return generate_key()


@pytest.fixture
def tmp_dir() -> Iterator[Path]:
    """A fresh temporary directory.

    Used instead of pytest's ``tmp_path``, whose "current" symlink creation stalls for ~30s
    per call on some Windows configurations.
    """
    with tempfile.TemporaryDirectory(prefix="meip-test-") as name:
        yield Path(name)


@pytest.fixture(scope="session")
def blob_root() -> Iterator[Path]:
    """Filesystem blob store for the test session (tests only; forbidden when deployed)."""
    root = Path(tempfile.mkdtemp(prefix="meip-blobs-"))
    yield root
    shutil.rmtree(root, ignore_errors=True)


@pytest.fixture(scope="session")
def settings(blob_root: Path) -> Settings:
    return Settings(
        env=Environment.TEST,
        auth_mode=AuthMode.DEV,
        database_url=db_url("meip_app", TEST_DB),
        database_pool_size=5,
        dev_auth_issuer=ISSUER,
        dev_auth_audience=AUDIENCE,
        dev_auth_key_path=Path(tempfile.gettempdir()) / "meip-test-unused.pem",
        log_level="WARNING",
        documents_enabled=True,
        blob_backend=BlobBackend.FILESYSTEM,
        blob_root=blob_root,
        document_max_bytes=3 * 1024 * 1024,
    )


@pytest.fixture(scope="session")
def app(owner_engine: AsyncEngine, settings: Settings, signing_key: RSAPrivateKey) -> FastAPI:
    provider = JWTAuthProvider(
        issuer=ISSUER, audience=AUDIENCE, key_resolver=StaticKeyResolver(signing_key.public_key())
    )
    return create_app(settings, auth_provider=provider)


@pytest.fixture(scope="session")
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# --- Firms, users, tokens ---------------------------------------------------------------


@dataclass
class Firm:
    """A tenant created for one test, with helpers to act as its members."""

    tenant_id: uuid.UUID
    admin_subject: str
    admin_membership_id: uuid.UUID
    key: RSAPrivateKey
    client: httpx.AsyncClient
    members: dict[str, uuid.UUID] = field(default_factory=dict)

    def headers(self, subject: str | None = None) -> dict[str, str]:
        token = mint_token(
            self.key, issuer=ISSUER, audience=AUDIENCE, subject=subject or self.admin_subject
        )
        return {"Authorization": f"Bearer {token}", "X-Tenant-ID": str(self.tenant_id)}

    async def request(
        self,
        method: str,
        path: str,
        *,
        as_: str | None = None,
        if_match: int | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        headers = {**self.headers(as_), **kwargs.pop("headers", {})}
        if if_match is not None:
            headers["If-Match"] = f'"{if_match}"'
        return await self.client.request(method, path, headers=headers, **kwargs)

    def bearer(self, subject: str) -> dict[str, str]:
        """Authorization only, without a tenant: for /me and invitation acceptance."""
        token = mint_token(self.key, issuer=ISSUER, audience=AUDIENCE, subject=subject)
        return {"Authorization": f"Bearer {token}"}

    async def add_member(
        self, role: str, subject: str | None = None, *, accept: bool = True
    ) -> str:
        """Invite ``subject`` (a new identity by default) and, unless ``accept=False``,
        accept the invitation as that person so the membership is active."""
        subject = subject or f"user-{uuid.uuid4()}"
        resp = await self.request(
            "POST",
            "/api/v1/members",
            json={"idp_subject": subject, "role": role, "email": f"{subject}@example.test"},
        )
        assert resp.status_code == 201, resp.text
        membership_id = uuid.UUID(resp.json()["membership_id"])
        self.members[subject] = membership_id
        if accept:
            accepted = await self.accept(subject, membership_id)
            assert accepted.status_code == 200, accepted.text
        return subject

    async def accept(self, subject: str, membership_id: uuid.UUID | str) -> httpx.Response:
        return await self.client.post(
            f"/api/v1/me/invitations/{membership_id}/accept", headers=self.bearer(subject)
        )

    async def create_case(self, *, as_: str | None = None, **body: Any) -> dict[str, Any]:
        payload = {"purpose": "purchase", **body}
        resp = await self.request("POST", "/api/v1/cases", as_=as_, json=payload)
        assert resp.status_code == 201, resp.text
        data: dict[str, Any] = resp.json()
        return data

    async def upload(
        self,
        case_id: str,
        content: bytes | None = None,
        *,
        as_: str | None = None,
        applicant_id: str | None = None,
        filename: str | None = None,
        content_type: str = "application/pdf",
    ) -> httpx.Response:
        headers = {"Content-Type": content_type}
        if filename is not None:
            headers["Upload-Filename"] = filename
        params = {"document_type": "payslip"}
        if applicant_id is not None:
            params["applicant_id"] = applicant_id
        return await self.request(
            "POST",
            f"/api/v1/cases/{case_id}/documents",
            as_=as_,
            params=params,
            content=synthetic_pdf() if content is None else content,
            headers=headers,
        )

    async def add_applicant(self, case_id: str, **body: Any) -> dict[str, Any]:
        payload = {"first_name": "Alex", "last_name": "Example", **body}
        resp = await self.request("POST", f"/api/v1/cases/{case_id}/applicants", json=payload)
        assert resp.status_code == 201, resp.text
        data: dict[str, Any] = resp.json()
        return data


MakeFirm = Callable[..., Awaitable["Firm"]]
CountAudit = Callable[[uuid.UUID], Awaitable[int]]


@pytest.fixture
async def make_firm(
    owner_sessionmaker: async_sessionmaker[AsyncSession],
    signing_key: RSAPrivateKey,
    client: httpx.AsyncClient,
) -> MakeFirm:
    async def _make(name: str = "Test Brokers Ltd") -> Firm:
        subject = f"admin-{uuid.uuid4()}"
        async with owner_sessionmaker() as session, session.begin():
            result = await onboard_tenant(
                session,
                name=name,
                fca_frn="123456",
                admin_issuer=ISSUER,
                admin_subject=subject,
                admin_email=f"{subject}@example.test",
                admin_display_name="Firm Admin",
                operator="operator:pytest",
            )
        return Firm(
            tenant_id=result.tenant_id,
            admin_subject=subject,
            admin_membership_id=result.admin_membership_id,
            key=signing_key,
            client=client,
        )

    return _make


@pytest.fixture
async def firm(make_firm: MakeFirm) -> Firm:
    return await make_firm()


@pytest.fixture
def audit_count(owner_sessionmaker: async_sessionmaker[AsyncSession]) -> CountAudit:
    """Count audit events for a tenant, read with the owner role (bypassing RLS)."""

    async def _count(tenant_id: uuid.UUID) -> int:
        async with owner_sessionmaker() as session:
            n = await session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.tenant_id == tenant_id)
            )
        return int(n or 0)

    return _count
