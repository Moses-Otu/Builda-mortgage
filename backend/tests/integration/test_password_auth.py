"""Phase 4: email and password accounts (``MEIP_AUTH_MODE=password``).

Sign-in, temporary passwords that can only be changed, lockout, firm onboarding and member
invitations by email, and an administrator's reset that can't reach another firm's access.
Firms, memberships, roles and tenant isolation are the existing ones."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from meip.app import create_app
from meip.auth.accounts import create_account
from meip.auth.factory import build_auth_provider
from meip.core.config import AuthMode, Settings
from meip.tenancy.service import onboard_tenant

ISSUER = "meip-accounts"


@pytest.fixture
def password_settings(settings: Settings, tmp_path: Path) -> Settings:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    path = tmp_path / "signing.pem"
    path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return settings.model_copy(
        update={"auth_mode": AuthMode.PASSWORD, "auth_signing_key_path": path}
    )


@pytest.fixture
async def api(
    password_settings: Settings, owner_engine: AsyncEngine
) -> AsyncIterator[httpx.AsyncClient]:
    app: FastAPI = create_app(
        password_settings, auth_provider=build_auth_provider(password_settings)
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


async def _firm(
    owner_sessionmaker: async_sessionmaker[AsyncSession], email: str
) -> tuple[uuid.UUID, str]:
    async with owner_sessionmaker() as session, session.begin():
        result = await onboard_tenant(
            session,
            name="Pilot Brokers Ltd",
            fca_frn="123456",
            admin_issuer=ISSUER,
            admin_subject=email,
            admin_email=email,
            admin_display_name="Pilot Admin",
            operator="operator:pytest",
        )
        temporary = await create_account(session, email)
    assert temporary
    return result.tenant_id, temporary


async def _login(api: httpx.AsyncClient, email: str, password: str) -> httpx.Response:
    return await api.post("/api/v1/auth/login", json={"email": email, "password": password})


async def _set_password(api: httpx.AsyncClient, email: str, temporary: str, new: str) -> str:
    first = await _login(api, email, temporary)
    assert first.status_code == 200, first.text
    assert first.json()["must_change_password"] is True
    token = first.json()["access_token"]
    resp = await api.post(
        "/api/v1/auth/password",
        headers={"authorization": f"Bearer {token}"},
        json={"current_password": temporary, "new_password": new},
    )
    assert resp.status_code == 204, resp.text
    second = await _login(api, email, new)
    assert second.status_code == 200, second.text
    assert second.json()["must_change_password"] is False
    out: str = second.json()["access_token"]
    return out


def _auth(token: str, tenant: uuid.UUID | None = None) -> dict[str, str]:
    headers = {"authorization": f"Bearer {token}"}
    if tenant:
        headers["x-tenant-id"] = str(tenant)
    return headers


async def test_a_firm_admin_signs_in_changes_the_temporary_password_and_works(
    api: httpx.AsyncClient, owner_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    email = f"admin-{uuid.uuid4().hex[:8]}@pilot.test"
    tenant, temporary = await _firm(owner_sessionmaker, email)

    first = await _login(api, email.upper(), temporary)  # emails are case-insensitive
    token = first.json()["access_token"]
    # A temporary-password token can only set a new password.
    refused = await api.get("/api/v1/me", headers=_auth(token))
    assert refused.status_code == 403
    assert refused.json()["type"].endswith("password-change-required")
    assert (await api.get("/api/v1/cases", headers=_auth(token, tenant))).status_code == 403
    # Weak passwords are refused.
    weak = await api.post(
        "/api/v1/auth/password",
        headers=_auth(token),
        json={"current_password": temporary, "new_password": "short"},
    )
    assert weak.status_code == 422

    full = await _set_password(api, email, temporary, "correct horse battery staple")
    me = await api.get("/api/v1/me", headers=_auth(full))
    assert me.status_code == 200
    assert [m["tenant_id"] for m in me.json()["memberships"]] == [str(tenant)]
    cases = await api.get("/api/v1/cases", headers=_auth(full, tenant))
    assert cases.status_code == 200
    # The temporary password no longer works.
    assert (await _login(api, email, temporary)).status_code == 401


async def test_wrong_passwords_lock_the_account_and_unknown_emails_look_the_same(
    api: httpx.AsyncClient, owner_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    email = f"admin-{uuid.uuid4().hex[:8]}@pilot.test"
    _, temporary = await _firm(owner_sessionmaker, email)
    unknown = await _login(api, "nobody@pilot.test", "whatever")
    wrong = await _login(api, email, "not the password")
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json()["detail"] == wrong.json()["detail"]
    for _ in range(4):
        await _login(api, email, "not the password")
    locked = await _login(api, email, temporary)  # even the right password, while locked
    assert locked.status_code == 401
    assert "Too many attempts" in locked.json()["detail"]


async def test_members_are_invited_by_email_and_reset_only_within_one_firm(
    api: httpx.AsyncClient, owner_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    admin = f"admin-{uuid.uuid4().hex[:8]}@pilot.test"
    tenant, temporary = await _firm(owner_sessionmaker, admin)
    admin_token = await _set_password(api, admin, temporary, "admin passphrase 2026")

    adviser = f"adviser-{uuid.uuid4().hex[:8]}@pilot.test"
    added = await api.post(
        "/api/v1/members",
        headers=_auth(admin_token, tenant),
        json={"idp_subject": adviser.title(), "email": adviser, "role": "adviser"},
    )
    assert added.status_code == 201, added.text
    member: dict[str, Any] = added.json()
    assert member["status"] == "invited"
    adviser_token = await _set_password(
        api, adviser, member["temporary_password"], "adviser passphrase 2026"
    )
    # The invited adviser accepts, then works in the firm.
    me = (await api.get("/api/v1/me", headers=_auth(adviser_token))).json()
    [invitation] = me["invitations"]
    accepted = await api.post(
        f"/api/v1/me/invitations/{invitation['membership_id']}/accept", headers=_auth(adviser_token)
    )
    assert accepted.status_code == 200, accepted.text
    assert (await api.get("/api/v1/cases", headers=_auth(adviser_token, tenant))).status_code == 200
    # An adviser can't reset passwords.
    refused = await api.post(
        f"/api/v1/members/{member['membership_id']}/password-reset",
        headers=_auth(adviser_token, tenant),
    )
    assert refused.status_code == 403
    # The admin can, within this firm: a new temporary password, shown once.
    reset = await api.post(
        f"/api/v1/members/{member['membership_id']}/password-reset",
        headers=_auth(admin_token, tenant),
    )
    assert reset.status_code == 200, reset.text
    assert (await _login(api, adviser, "adviser passphrase 2026")).status_code == 401
    again = await _login(api, adviser, reset.json()["temporary_password"])
    assert again.json()["must_change_password"] is True
    async with owner_sessionmaker() as session:
        from sqlalchemy import text

        events = (
            (
                await session.execute(
                    text(
                        "SELECT changes::text FROM audit_events WHERE tenant_id = :t "
                        "AND action = 'member.password_reset'"
                    ),
                    {"t": tenant},
                )
            )
            .scalars()
            .all()
        )
    assert len(events) == 1
    assert reset.json()["temporary_password"] not in events[0]  # the password is never recorded

    # Once the adviser also belongs to another firm, this firm's admin can't reset them.
    other_admin = f"admin-{uuid.uuid4().hex[:8]}@pilot.test"
    other, other_temp = await _firm(owner_sessionmaker, other_admin)
    other_token = await _set_password(api, other_admin, other_temp, "other passphrase 2026")
    invited = await api.post(
        "/api/v1/members",
        headers=_auth(other_token, other),
        json={"idp_subject": adviser, "email": adviser, "role": "adviser"},
    )
    assert invited.status_code == 201
    assert invited.json()["temporary_password"] is None  # an existing account keeps its password
    adviser_token = await _set_password(
        api, adviser, reset.json()["temporary_password"], "adviser passphrase 2027"
    )
    second = (await api.get("/api/v1/me", headers=_auth(adviser_token))).json()["invitations"][0]
    await api.post(
        f"/api/v1/me/invitations/{second['membership_id']}/accept", headers=_auth(adviser_token)
    )
    blocked = await api.post(
        f"/api/v1/members/{member['membership_id']}/password-reset",
        headers=_auth(admin_token, tenant),
    )
    assert blocked.status_code == 409


async def test_password_routes_are_absent_in_other_modes(client: httpx.AsyncClient) -> None:
    resp = await client.post("/api/v1/auth/login", json={"email": "a@b.test", "password": "x"})
    assert resp.status_code == 404
