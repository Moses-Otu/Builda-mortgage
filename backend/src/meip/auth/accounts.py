"""Email and password accounts for the pilot (Phase 4, ``MEIP_AUTH_MODE=password``).

The platform issues its own short-lived RS256 tokens, verified by the same
:class:`~meip.auth.provider.JWTAuthProvider` as any OpenID Connect token: an account's email
is the identity's subject under the platform's issuer, so firms, memberships, roles and tenant
isolation are unchanged.

* **Hashing:** scrypt (standard library), a fresh 16-byte salt per password, constant-time
  comparison. A sign-in for an unknown email still computes a hash, so timing doesn't reveal
  which emails exist.
* **Temporary passwords:** a new account (or a firm administrator's reset) gets a random
  password shown once. A token issued for it carries ``pwd_change`` and can only be used to set
  a new password.
* **Lockout:** five failed attempts lock the account for fifteen minutes.
* **Storage:** hashes live in ``local_accounts``, which the runtime role reaches only through
  two security-definer functions. Nothing here logs emails or passwords.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from meip.auth.dev import mint_token
from meip.core.errors import BusinessRuleError, UnauthorizedError

MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 256
MAX_FAILED_ATTEMPTS = 5
LOCKOUT = timedelta(minutes=15)
_N, _R, _P = 2**15, 8, 1
_DUMMY = "scrypt$32768$8$1$AAAAAAAAAAAAAAAAAAAAAA==$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="


def normalise_email(email: str) -> str:
    return email.strip().lower()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode(), salt=salt, n=_N, r=_R, p=_P, maxmem=128 * 2**20, dklen=32
    )
    return "$".join(
        [
            "scrypt",
            str(_N),
            str(_R),
            str(_P),
            base64.b64encode(salt).decode(),
            base64.b64encode(digest).decode(),
        ]
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_b64, digest_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(digest_b64)
        digest = hashlib.scrypt(
            password.encode(),
            salt=base64.b64decode(salt_b64),
            n=int(n),
            r=int(r),
            p=int(p),
            maxmem=128 * 2**20,
            dklen=len(expected),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest, expected)


def check_new_password(password: str, email: str) -> None:
    if not MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH:
        raise BusinessRuleError(f"Use a password of at least {MIN_PASSWORD_LENGTH} characters")
    if normalise_email(email).split("@")[0] in password.lower():
        raise BusinessRuleError("The password must not contain your email name")


def temporary_password() -> str:
    """Random, shown once; must be changed at first sign-in."""
    return secrets.token_urlsafe(15)


@dataclass(frozen=True)
class Account:
    password_hash: str
    must_change_password: bool
    failed_attempts: int
    locked_until: datetime | None


async def _account(session: AsyncSession, email: str) -> Account | None:
    row = (
        await session.execute(text("SELECT * FROM app_local_account(:email)"), {"email": email})
    ).one_or_none()
    return Account(*row) if row is not None else None


async def _write(
    session: AsyncSession,
    email: str,
    *,
    password_hash: str | None,
    must_change: bool | None,
    failed: int,
    locked_until: datetime | None,
    create: bool = False,
) -> bool:
    result = await session.scalar(
        text(
            "SELECT app_local_account_write(:email, :hash, :must_change, :failed, :locked, :create)"
        ),
        {
            "email": email,
            "hash": password_hash,
            "must_change": must_change,
            "failed": failed,
            "locked": locked_until,
            "create": create,
        },
    )
    return bool(result)


async def create_account(session: AsyncSession, email: str) -> str | None:
    """Create an account with a temporary password, returned once. ``None`` if the email
    already has an account (that person signs in with their own password)."""
    password = temporary_password()
    created = await _write(
        session,
        normalise_email(email),
        password_hash=hash_password(password),
        must_change=True,
        failed=0,
        locked_until=None,
        create=True,
    )
    return password if created else None


async def reset_password(session: AsyncSession, email: str) -> str:
    """A new temporary password, returned once; the old one stops working."""
    password = temporary_password()
    updated = await _write(
        session,
        normalise_email(email),
        password_hash=hash_password(password),
        must_change=True,
        failed=0,
        locked_until=None,
    )
    if not updated:
        raise BusinessRuleError("This member has no password account")
    return password


@dataclass(frozen=True)
class SignedIn:
    token: str
    expires_in: int
    must_change_password: bool


async def sign_in(
    session: AsyncSession,
    email: str,
    password: str,
    *,
    key: RSAPrivateKey,
    issuer: str,
    audience: str,
    ttl: timedelta,
    now: datetime | None = None,
) -> SignedIn:
    when = now or datetime.now(UTC)
    address = normalise_email(email)
    account = await _account(session, address)
    if account is None:
        verify_password(password, _DUMMY)  # same work either way
        raise UnauthorizedError("Email or password is incorrect")
    if account.locked_until is not None and account.locked_until > when:
        verify_password(password, _DUMMY)
        raise UnauthorizedError("Too many attempts. Try again in a few minutes.")
    if not verify_password(password, account.password_hash):
        failed = account.failed_attempts + 1
        locked = when + LOCKOUT if failed >= MAX_FAILED_ATTEMPTS else None
        await _write(
            session,
            address,
            password_hash=None,
            must_change=None,
            failed=0 if locked else failed,
            locked_until=locked,
        )
        raise UnauthorizedError("Email or password is incorrect")
    await _write(
        session, address, password_hash=None, must_change=None, failed=0, locked_until=None
    )
    token = mint_token(
        key,
        issuer=issuer,
        audience=audience,
        subject=address,
        email=address,
        ttl=ttl,
        now=when,
        extra={"pwd_change": True} if account.must_change_password else None,
    )
    return SignedIn(token, int(ttl.total_seconds()), account.must_change_password)


async def change_password(session: AsyncSession, email: str, current: str, new: str) -> None:
    address = normalise_email(email)
    account = await _account(session, address)
    if account is None or not verify_password(current, account.password_hash):
        raise UnauthorizedError("The current password is incorrect")
    check_new_password(new, address)
    if verify_password(new, account.password_hash):
        raise BusinessRuleError("Choose a password different from the current one")
    await _write(
        session,
        address,
        password_hash=hash_password(new),
        must_change=False,
        failed=0,
        locked_until=None,
    )


def load_signing_key(path: Path) -> RSAPrivateKey:
    key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    if not isinstance(key, RSAPrivateKey):
        raise ValueError("The signing key must be an RSA private key")
    return key
