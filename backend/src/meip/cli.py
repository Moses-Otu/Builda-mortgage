"""Platform operator CLI: ``meip-admin``.

Commands:
  create-tenant  Onboard a broker firm and its first firm administrator (owner DB role).
  dev-token      Mint a local development bearer token (auth_mode=dev only).
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys
from collections.abc import Sequence
from datetime import timedelta

from pydantic import ValidationError
from sqlalchemy import text

from meip.auth.dev import load_or_create_key, mint_token
from meip.core.config import AuthMode, ConfigurationError, Settings, get_settings
from meip.core.db import create_engine, create_sessionmaker
from meip.documents.storage import AzureBlobStore, build_blob_store, orphaned_keys
from meip.tenancy.service import onboard_tenant


def _load_settings() -> Settings | None:
    try:
        return get_settings()
    except (ValidationError, ConfigurationError) as exc:
        # Report which settings are wrong without echoing their values.
        if isinstance(exc, ValidationError):
            fields = ", ".join(".".join(map(str, e["loc"])) for e in exc.errors())
            print(f"Invalid configuration: {fields} (check MEIP_* variables)", file=sys.stderr)
        else:
            print(f"Invalid configuration: {exc}", file=sys.stderr)
        return None


def _operator_ref(explicit: str | None) -> str | None:
    operator = (explicit or "").strip() or getpass.getuser().strip()
    return f"operator:{operator}" if operator else None


async def _create_tenant(args: argparse.Namespace) -> int:
    owner_url = os.environ.get("MEIP_MIGRATIONS_DATABASE_URL")
    if not owner_url:
        print("MEIP_MIGRATIONS_DATABASE_URL (owner role) must be set", file=sys.stderr)
        return 2
    settings = _load_settings()
    if settings is None:
        return 2
    operator = _operator_ref(args.operator)
    if operator is None:
        print("Cannot determine the operator; pass --operator", file=sys.stderr)
        return 2
    issuer = args.issuer or (
        settings.dev_auth_issuer if settings.auth_mode is AuthMode.DEV else settings.oidc_issuer
    )
    if not issuer:
        print("Cannot determine identity-provider issuer; pass --issuer", file=sys.stderr)
        return 2

    engine = create_engine(owner_url, pool_size=1)
    try:
        async with create_sessionmaker(engine)() as session, session.begin():
            result = await onboard_tenant(
                session,
                name=args.name,
                fca_frn=args.fca_frn,
                admin_issuer=issuer,
                admin_subject=args.admin_subject,
                admin_email=args.admin_email,
                admin_display_name=args.admin_name,
                operator=operator,
            )
    finally:
        await engine.dispose()
    print(f"tenant_id={result.tenant_id}")
    print(f"admin_membership_id={result.admin_membership_id}")
    return 0


def _dev_token(args: argparse.Namespace) -> int:
    settings = _load_settings()
    if settings is None:
        return 2
    if settings.auth_mode is not AuthMode.DEV:
        print("dev-token requires MEIP_AUTH_MODE=dev", file=sys.stderr)
        return 2
    key = load_or_create_key(settings.dev_auth_key_path)
    print(
        mint_token(
            key,
            issuer=settings.dev_auth_issuer,
            audience=settings.dev_auth_audience,
            subject=args.subject,
            email=args.email,
            name=args.name,
            ttl=timedelta(minutes=args.ttl_minutes),
        )
    )
    return 0


async def _init_storage() -> int:
    """Create the private blob container (local bootstrap; Azure containers come from IaC)."""
    settings = _load_settings()
    if settings is None:
        return 2
    store = build_blob_store(settings)
    if not isinstance(store, AzureBlobStore):
        print("Nothing to do: the filesystem backend needs no container")
        return 0
    created = await store.ensure_container()
    print("container created" if created else "container already exists")
    return 0


async def _find_orphan_blobs() -> int:
    """Read-only report: stored originals with no database row (left by a crash between
    storing a blob and committing its row). Deleting them is an operator decision."""
    owner_url = os.environ.get("MEIP_MIGRATIONS_DATABASE_URL")
    if not owner_url:
        print("MEIP_MIGRATIONS_DATABASE_URL (owner role) must be set", file=sys.stderr)
        return 2
    settings = _load_settings()
    if settings is None:
        return 2
    engine = create_engine(owner_url, pool_size=1)
    try:
        async with engine.connect() as conn:
            recorded = set(
                (await conn.execute(text("SELECT blob_key FROM document_versions"))).scalars()
            )
    finally:
        await engine.dispose()
    orphans = orphaned_keys(await build_blob_store(settings).list_keys(), recorded)
    for key in orphans:
        print(key)
    print(f"{len(orphans)} orphaned blob(s)", file=sys.stderr)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="meip-admin")
    sub = parser.add_subparsers(dest="command", required=True)

    t = sub.add_parser("create-tenant", help="Onboard a broker firm")
    t.add_argument("--name", required=True)
    t.add_argument("--fca-frn", default=None, help="FCA Firm Reference Number (6-7 digits)")
    t.add_argument("--admin-subject", required=True, help="IdP subject of the first admin")
    t.add_argument("--admin-email", default=None)
    t.add_argument("--admin-name", default=None)
    t.add_argument("--issuer", default=None, help="IdP issuer (defaults from settings)")
    t.add_argument(
        "--operator",
        default=None,
        help="Who is running this command, recorded in the audit log (default: OS user)",
    )

    d = sub.add_parser("dev-token", help="Mint a local development token")
    d.add_argument("--subject", required=True)
    d.add_argument("--email", default=None)
    d.add_argument("--name", default=None)
    d.add_argument("--ttl-minutes", type=int, default=60)

    sub.add_parser("init-storage", help="Create the private blob container (local only)")
    sub.add_parser(
        "find-orphan-blobs", help="List stored originals with no database row (read-only)"
    )

    args = parser.parse_args(argv)
    if args.command == "create-tenant":
        return asyncio.run(_create_tenant(args))
    if args.command == "init-storage":
        return asyncio.run(_init_storage())
    if args.command == "find-orphan-blobs":
        return asyncio.run(_find_orphan_blobs())
    return _dev_token(args)


if __name__ == "__main__":
    raise SystemExit(main())
