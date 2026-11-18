"""Authentication and tenant-access behaviour at the API boundary."""

from __future__ import annotations

import uuid

import httpx
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from meip.auth.dev import generate_key, mint_token
from meip.tenancy.models import Tenant
from tests.conftest import AUDIENCE, ISSUER, Firm, MakeFirm


async def test_missing_token_is_401_problem(client: httpx.AsyncClient, firm: Firm) -> None:
    resp = await client.get("/api/v1/cases", headers={"X-Tenant-ID": str(firm.tenant_id)})
    assert resp.status_code == 401
    assert resp.headers["content-type"] == "application/problem+json"
    assert resp.headers["www-authenticate"] == "Bearer"
    body = resp.json()
    assert body["status"] == 401
    assert body["type"].endswith(":unauthorized")
    assert body["request_id"] == resp.headers["x-request-id"]


async def test_forged_token_is_401(client: httpx.AsyncClient, firm: Firm) -> None:
    forged = mint_token(
        generate_key(), issuer=ISSUER, audience=AUDIENCE, subject=firm.admin_subject
    )
    resp = await client.get(
        "/api/v1/cases",
        headers={"Authorization": f"Bearer {forged}", "X-Tenant-ID": str(firm.tenant_id)},
    )
    assert resp.status_code == 401


async def test_missing_tenant_header_is_422(firm: Firm) -> None:
    headers = firm.headers()
    del headers["X-Tenant-ID"]
    resp = await firm.client.get("/api/v1/cases", headers=headers)
    assert resp.status_code == 422
    assert resp.json()["errors"][0]["loc"] == ["header", "X-Tenant-ID"]


async def test_unknown_tenant_and_foreign_tenant_both_403(firm: Firm, make_firm: MakeFirm) -> None:
    other = await make_firm()
    for tenant in (uuid.uuid4(), other.tenant_id):
        headers = {**firm.headers(), "X-Tenant-ID": str(tenant)}
        resp = await firm.client.get("/api/v1/cases", headers=headers)
        assert resp.status_code == 403
        assert resp.json()["detail"] == "You do not have access to this firm"


async def test_me_lists_memberships_across_firms(firm: Firm, make_firm: MakeFirm) -> None:
    other = await make_firm("Second Firm")
    await other.add_member("read_only", subject=firm.admin_subject)

    resp = await firm.client.get(
        "/api/v1/me", headers={"Authorization": firm.headers()["Authorization"]}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["subject"] == firm.admin_subject
    roles = {(m["tenant_id"], m["role"]) for m in body["memberships"]}
    assert roles == {(str(firm.tenant_id), "firm_admin"), (str(other.tenant_id), "read_only")}


async def test_me_for_unknown_identity(
    client: httpx.AsyncClient, signing_key: RSAPrivateKey
) -> None:
    token = mint_token(signing_key, issuer=ISSUER, audience=AUDIENCE, subject="nobody")
    resp = await client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["user_id"] is None
    assert resp.json()["memberships"] == []


async def test_suspended_tenant_denied(
    firm: Firm, owner_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    async with owner_sessionmaker() as session, session.begin():
        await session.execute(
            update(Tenant).where(Tenant.id == firm.tenant_id).values(status="suspended")
        )
    resp = await firm.request("GET", "/api/v1/cases")
    assert resp.status_code == 403
    me = await firm.client.get(
        "/api/v1/me", headers={"Authorization": firm.headers()["Authorization"]}
    )
    assert me.json()["memberships"] == []


async def test_validation_errors_do_not_echo_input(firm: Firm) -> None:
    resp = await firm.request(
        "POST", "/api/v1/cases", json={"purpose": "purchase", "external_reference": "x" * 101}
    )
    assert resp.status_code == 422
    assert "xxxx" not in resp.text


async def test_unknown_fields_rejected(firm: Firm) -> None:
    resp = await firm.request("POST", "/api/v1/cases", json={"purpose": "purchase", "lender": "X"})
    assert resp.status_code == 422
