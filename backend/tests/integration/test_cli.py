"""The operator CLI, run as a real subprocess through its console-script entry point."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import jwt
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.conftest import TEST_DB, db_url


def _run(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed interpreter and arguments
        [sys.executable, "-m", "meip.cli", *args],
        # Start from a clean MEIP_* slate so the developer's shell cannot influence results.
        env={**{k: v for k, v in os.environ.items() if not k.startswith("MEIP_")}, **env},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_dev_token(tmp_dir: Path) -> None:
    env = {
        "MEIP_AUTH_MODE": "dev",
        "MEIP_ENV": "local",
        "MEIP_DEV_AUTH_KEY_PATH": str(tmp_dir / "k.pem"),
    }
    result = _run(["dev-token", "--subject", "alice", "--email", "a@x.test"], env)
    assert result.returncode == 0, result.stderr
    claims = jwt.decode(result.stdout.strip(), options={"verify_signature": False})
    assert claims["sub"] == "alice"
    assert claims["aud"] == "meip-api"


def test_dev_token_refused_in_oidc_mode() -> None:
    env = {
        "MEIP_ENV": "production",
        "MEIP_AUTH_MODE": "oidc",
        "MEIP_OIDC_ISSUER": "https://i/",
        "MEIP_OIDC_AUDIENCE": "a",
        "MEIP_OIDC_JWKS_URL": "https://i/k",
    }
    result = _run(["dev-token", "--subject", "alice"], env)
    assert result.returncode == 2
    assert "requires MEIP_AUTH_MODE=dev" in result.stderr


def test_missing_environment_is_refused_without_traceback() -> None:
    result = _run(["dev-token", "--subject", "alice"], {"MEIP_AUTH_MODE": "dev"})
    assert result.returncode == 2
    assert "Invalid configuration: env" in result.stderr
    assert "Traceback" not in result.stderr


def test_dev_auth_refused_in_production_by_cli() -> None:
    env = {"MEIP_ENV": "production", "MEIP_AUTH_MODE": "dev"}
    result = _run(["dev-token", "--subject", "alice"], env)
    assert result.returncode == 2
    assert "forbidden" in result.stderr


async def test_create_tenant(owner_engine: AsyncEngine, tmp_dir: Path) -> None:
    env = {
        "MEIP_AUTH_MODE": "dev",
        "MEIP_ENV": "local",
        "MEIP_DEV_AUTH_KEY_PATH": str(tmp_dir / "k.pem"),
        "MEIP_MIGRATIONS_DATABASE_URL": db_url("meip_owner", TEST_DB),
    }
    result = _run(
        [
            "create-tenant",
            "--name",
            "CLI Brokers",
            "--fca-frn",
            "654321",
            "--admin-subject",
            "cli-admin",
            "--operator",
            "ops.alice",
        ],
        env,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("tenant_id=")

    tenant_id = result.stdout.splitlines()[0].removeprefix("tenant_id=").strip()
    async with owner_engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT actor_type, actor_ref, actor_user_id FROM audit_events "
                    "WHERE tenant_id = :t AND action = 'tenant.created'"
                ),
                {"t": tenant_id},
            )
        ).one()
    assert tuple(row) == ("operator", "operator:ops.alice", None)


def test_create_tenant_requires_owner_url() -> None:
    env = {"MEIP_AUTH_MODE": "dev", "MEIP_ENV": "local", "MEIP_MIGRATIONS_DATABASE_URL": ""}
    result = _run(["create-tenant", "--name", "X", "--admin-subject", "s"], env)
    assert result.returncode == 2
