"""Platform operator CLI: ``meip-admin``.

Commands:
  create-tenant         Onboard a broker firm and its first firm administrator (owner DB
                        role). In password mode, prints the admin's temporary password once.
  generate-signing-key  Create the RSA key that signs password-mode tokens (Phase 4).
  erase-case            Permanently erase one case and its documents (owner DB role).
  retention-sweep       List closed cases past the retention period; --apply erases them.
  bootstrap-database    Create the owner and runtime roles and the database on a managed
                        PostgreSQL (superuser URL; idempotent). For hosts without init scripts.
  dev-token             Mint a local development bearer token (auth_mode=dev only).
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys
import uuid
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
    password_mode = settings.auth_mode is AuthMode.PASSWORD
    if password_mode:
        if not args.admin_email:
            print("Password mode: --admin-email is required (it is the sign-in)", file=sys.stderr)
            return 2
        from meip.auth.accounts import normalise_email

        args.admin_subject = normalise_email(args.admin_email)
        args.admin_email = args.admin_subject
    if not args.admin_subject:
        print("--admin-subject is required (the identity provider's subject)", file=sys.stderr)
        return 2
    issuer = args.issuer or (
        settings.dev_auth_issuer
        if settings.auth_mode is AuthMode.DEV
        else settings.password_auth_issuer
        if password_mode
        else settings.oidc_issuer
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
            temporary = None
            if password_mode:
                from meip.auth.accounts import create_account

                temporary = await create_account(session, args.admin_subject)
    finally:
        await engine.dispose()
    print(f"tenant_id={result.tenant_id}")
    print(f"admin_membership_id={result.admin_membership_id}")
    if password_mode:
        if temporary:
            print(f"admin_sign_in={args.admin_subject}")
            print(f"temporary_password={temporary}  (shown once; must be changed at sign-in)")
        else:
            print("This email already has an account: the admin signs in with their password.")
    return 0


async def _erase(
    owner_url: str,
    settings: Settings,
    cases: list[tuple[uuid.UUID, uuid.UUID]],
    operator: str,
    reason: str,
) -> int:
    from meip.retention.blobs import erase_blob
    from meip.retention.erasure import erase_case

    store = build_blob_store(settings)
    engine = create_engine(owner_url, pool_size=1)
    failures = 0
    try:
        for tenant_id, case_id in cases:
            async with create_sessionmaker(engine)() as session, session.begin():
                erased = await erase_case(
                    session, tenant_id, case_id, operator=operator, reason=reason
                )
            for key in erased.blob_keys:  # only after the database commit
                try:
                    if not await erase_blob(store, key):
                        raise OSError
                except Exception:
                    erased.blobs_not_deleted.append(key)
            failures += len(erased.blobs_not_deleted)
            print(
                f"erased case={case_id} rows={sum(erased.rows.values())} "
                f"documents={len(erased.blob_keys)} audit_details={erased.audit_details} "
                f"blobs_not_deleted={len(erased.blobs_not_deleted)}"
            )
            for key in erased.blobs_not_deleted:
                print(f"  NOT DELETED: {key}", file=sys.stderr)
    finally:
        await engine.dispose()
    return 1 if failures else 0


async def _erase_case(args: argparse.Namespace) -> int:
    owner_url = os.environ.get("MEIP_MIGRATIONS_DATABASE_URL")
    settings = _load_settings()
    operator = _operator_ref(args.operator)
    if not owner_url or settings is None or operator is None:
        print(
            "Needs MEIP_MIGRATIONS_DATABASE_URL (owner role), valid settings and an operator",
            file=sys.stderr,
        )
        return 2
    if not args.confirm:
        print("Erasure is permanent. Re-run with --confirm to erase.", file=sys.stderr)
        return 2
    try:
        return await _erase(
            owner_url,
            settings,
            [(uuid.UUID(args.tenant), uuid.UUID(args.case))],
            operator,
            args.reason,
        )
    except LookupError as exc:
        print(str(exc), file=sys.stderr)
        return 2


async def _retention_sweep(args: argparse.Namespace) -> int:
    from meip.retention.erasure import expired_cases

    owner_url = os.environ.get("MEIP_MIGRATIONS_DATABASE_URL")
    settings = _load_settings()
    operator = _operator_ref(args.operator)
    if not owner_url or settings is None or operator is None:
        print(
            "Needs MEIP_MIGRATIONS_DATABASE_URL (owner role), valid settings and an operator",
            file=sys.stderr,
        )
        return 2
    engine = create_engine(owner_url, pool_size=1)
    try:
        async with create_sessionmaker(engine)() as session:
            due = await expired_cases(session, args.days_after_closure)
    finally:
        await engine.dispose()
    for tenant_id, case_id, closed in due:
        print(f"due tenant={tenant_id} case={case_id} closed={closed:%Y-%m-%d}")
    print(f"{len(due)} closed case(s) older than {args.days_after_closure} days", file=sys.stderr)
    if not args.apply or not due:
        if due:
            print("Dry run. Re-run with --apply to erase them.", file=sys.stderr)
        return 0
    return await _erase(
        owner_url,
        settings,
        [(t, c) for t, c, _ in due],
        operator,
        f"Retention: closed more than {args.days_after_closure} days",
    )


def _generate_signing_key(args: argparse.Namespace) -> int:
    """A new RSA key for password-mode tokens, written with owner-only permissions."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    target = os.path.abspath(args.out)
    if os.path.exists(target):
        print(
            f"{target} exists; refusing to overwrite (rotating it signs everyone out)",
            file=sys.stderr,
        )
        return 2
    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as out:
        out.write(pem)
    print(f"Wrote {target}. Keep it secret; set MEIP_AUTH_SIGNING_KEY_PATH to it.")
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


_BOOTSTRAP_ROLES = {
    "meip_owner": "LOGIN NOSUPERUSER NOCREATEROLE",
    # Runtime role: not a table owner, cannot bypass row-level security.
    "meip_app": "LOGIN NOSUPERUSER NOCREATEROLE NOBYPASSRLS",
}


async def _bootstrap_database() -> int:
    """The same roles and database as ``scripts/db`` and the pilot's ``db-init``, for managed
    PostgreSQL where init scripts can't run (the QA deployment). Safe to repeat: existing
    roles get their passwords re-set, an existing database is kept."""
    import asyncpg

    url = os.environ.get("MEIP_BOOTSTRAP_SUPERUSER_URL", "")
    passwords = {
        "meip_owner": os.environ.get("MEIP_OWNER_DB_PASSWORD", ""),
        "meip_app": os.environ.get("MEIP_APP_DB_PASSWORD", ""),
    }
    if not url or not all(passwords.values()):
        print(
            "Needs MEIP_BOOTSTRAP_SUPERUSER_URL, MEIP_OWNER_DB_PASSWORD and MEIP_APP_DB_PASSWORD",
            file=sys.stderr,
        )
        return 2
    if any(len(p) < 16 for p in passwords.values()):
        print("Database passwords must be at least 16 characters", file=sys.stderr)
        return 2
    conn = await asyncpg.connect(url.replace("postgresql+asyncpg://", "postgresql://", 1))
    try:
        for role, attributes in _BOOTSTRAP_ROLES.items():
            exists = await conn.fetchval("SELECT 1 FROM pg_roles WHERE rolname = $1", role)
            verb = "ALTER" if exists else "CREATE"
            # Role names are constants; the password is quoted by the server (format %L).
            statement = await conn.fetchval(
                f"SELECT format('{verb} ROLE {role} {attributes} PASSWORD %L', $1::text)",
                passwords[role],
            )
            await conn.execute(statement)
            print(f"role {role} {'updated' if exists else 'created'}")
        if await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = 'meip'"):
            print("database meip already exists")
        else:
            await conn.execute("CREATE DATABASE meip OWNER meip_owner")
            print("database meip created")
        await conn.execute("REVOKE ALL ON DATABASE meip FROM PUBLIC")
        await conn.execute("GRANT CONNECT ON DATABASE meip TO meip_app")
    finally:
        await conn.close()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="meip-admin")
    sub = parser.add_subparsers(dest="command", required=True)

    t = sub.add_parser("create-tenant", help="Onboard a broker firm")
    t.add_argument("--name", required=True)
    t.add_argument("--fca-frn", default=None, help="FCA Firm Reference Number (6-7 digits)")
    t.add_argument(
        "--admin-subject",
        default=None,
        help="IdP subject of the first admin (OIDC mode); password mode uses --admin-email",
    )
    t.add_argument("--admin-email", default=None)
    t.add_argument("--admin-name", default=None)
    t.add_argument("--issuer", default=None, help="IdP issuer (defaults from settings)")
    t.add_argument(
        "--operator",
        default=None,
        help="Who is running this command, recorded in the audit log (default: OS user)",
    )

    e = sub.add_parser("erase-case", help="Permanently erase one case (owner role)")
    e.add_argument("--tenant", required=True, help="The firm's tenant id")
    e.add_argument("--case", required=True, help="The case id")
    e.add_argument("--reason", required=True, help="Why: recorded in the audit details")
    e.add_argument("--confirm", action="store_true", help="Required: erasure is permanent")
    e.add_argument("--operator", default=None)

    r = sub.add_parser(
        "retention-sweep", help="Closed cases past retention (dry run unless --apply)"
    )
    r.add_argument(
        "--days-after-closure",
        type=int,
        required=True,
        help="The approved retention period (decision D10); no default on purpose",
    )
    r.add_argument("--apply", action="store_true", help="Erase them (permanent)")
    r.add_argument("--operator", default=None)

    k = sub.add_parser("generate-signing-key", help="Create the password-mode token signing key")
    k.add_argument("--out", required=True, help="Where to write the PEM file (a secret)")

    d = sub.add_parser("dev-token", help="Mint a local development token")
    d.add_argument("--subject", required=True)
    d.add_argument("--email", default=None)
    d.add_argument("--name", default=None)
    d.add_argument("--ttl-minutes", type=int, default=60)

    sub.add_parser(
        "bootstrap-database",
        help="Create roles and database on managed PostgreSQL (MEIP_BOOTSTRAP_SUPERUSER_URL)",
    )
    sub.add_parser("init-storage", help="Create the private blob container (local only)")
    sub.add_parser(
        "find-orphan-blobs", help="List stored originals with no database row (read-only)"
    )

    args = parser.parse_args(argv)
    if args.command == "create-tenant":
        return asyncio.run(_create_tenant(args))
    if args.command == "erase-case":
        return asyncio.run(_erase_case(args))
    if args.command == "retention-sweep":
        return asyncio.run(_retention_sweep(args))
    if args.command == "generate-signing-key":
        return _generate_signing_key(args)
    if args.command == "bootstrap-database":
        return asyncio.run(_bootstrap_database())
    if args.command == "init-storage":
        return asyncio.run(_init_storage())
    if args.command == "find-orphan-blobs":
        return asyncio.run(_find_orphan_blobs())
    return _dev_token(args)


if __name__ == "__main__":
    raise SystemExit(main())
