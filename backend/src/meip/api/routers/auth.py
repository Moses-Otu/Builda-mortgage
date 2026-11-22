"""Sign-in and password change for password-mode accounts (Phase 4 pilot).

Not tenant-scoped: an account belongs to a person, who may hold memberships in several firms.
Available only when ``MEIP_AUTH_MODE=password``; otherwise 404."""

from __future__ import annotations

from datetime import timedelta
from functools import lru_cache
from pathlib import Path
from typing import Annotated

import structlog
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from fastapi import APIRouter, Depends, status
from pydantic import Field, StringConstraints
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from meip.api.deps import get_principal_for_password_change, get_sessionmaker, get_settings
from meip.auth import accounts
from meip.auth.provider import Principal
from meip.core.config import AuthMode, Settings
from meip.core.errors import NotFoundError, UnauthorizedError
from meip.core.schemas import InputModel, OutputModel

router = APIRouter(prefix="/api/v1/auth", tags=["identity"])
_log = structlog.get_logger()

Email = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=3, max_length=320, pattern=r"^[^@\s]+@[^@\s]+$"
    ),
]
Password = Annotated[str, StringConstraints(min_length=1, max_length=accounts.MAX_PASSWORD_LENGTH)]


class SignInIn(InputModel):
    email: Email
    password: Password


class SignInOut(OutputModel):
    access_token: str
    token_type: str = "Bearer"  # noqa: S105 - the OAuth token type, not a secret
    expires_in: int = Field(description="Seconds")
    must_change_password: bool = Field(
        description="A temporary password: the token can only be used to set a new password"
    )


class PasswordChangeIn(InputModel):
    current_password: Password
    new_password: Password = Field(
        description=f"At least {accounts.MIN_PASSWORD_LENGTH} characters"
    )


@lru_cache(maxsize=4)
def _key(path: Path) -> RSAPrivateKey:
    return accounts.load_signing_key(path)


def _password_mode(settings: Annotated[Settings, Depends(get_settings)]) -> Settings:
    if settings.auth_mode is not AuthMode.PASSWORD or settings.auth_signing_key_path is None:
        raise NotFoundError("Not found")
    return settings


Sessions = Annotated[async_sessionmaker[AsyncSession], Depends(get_sessionmaker)]
PasswordMode = Annotated[Settings, Depends(_password_mode)]


@router.post(
    "/login",
    response_model=SignInOut,
    summary="Sign in with email and password",
    description=(
        "Returns a short-lived bearer token. Five failed attempts lock the account for 15 "
        "minutes. A temporary password gives a token that can only set a new password "
        "(`POST /api/v1/auth/password`). The same message is returned for an unknown email "
        "and a wrong password."
    ),
    responses={401: {"description": "Email or password incorrect, or temporarily locked"}},
)
async def login(body: SignInIn, settings: PasswordMode, sessionmaker: Sessions) -> SignInOut:
    assert settings.auth_signing_key_path  # noqa: S101 - checked by _password_mode
    async with sessionmaker() as session:
        try:
            signed = await accounts.sign_in(
                session,
                body.email,
                body.password,
                key=_key(settings.auth_signing_key_path),
                issuer=settings.password_auth_issuer,
                audience=settings.password_auth_audience,
                ttl=timedelta(minutes=settings.session_minutes),
            )
        except UnauthorizedError:
            # Keep the failed-attempt count and any lockout: commit before refusing.
            await session.commit()
            _log.info("sign_in_refused")  # no email, no reason: never a lookup oracle in logs
            raise
        await session.commit()
    _log.info("sign_in_succeeded")
    return SignInOut(
        access_token=signed.token,
        expires_in=signed.expires_in,
        must_change_password=signed.must_change_password,
    )


@router.post(
    "/password",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Set a new password",
    description=(
        f"Requires the current password. At least {accounts.MIN_PASSWORD_LENGTH} characters. "
        "Accepts a token issued for a temporary password. Sign in again afterwards."
    ),
)
async def change_password(
    body: PasswordChangeIn,
    _settings: PasswordMode,
    principal: Annotated[Principal, Depends(get_principal_for_password_change)],
    sessionmaker: Sessions,
) -> None:
    async with sessionmaker() as session, session.begin():
        await accounts.change_password(
            session, principal.subject, body.current_password, body.new_password
        )
    _log.info("password_changed")
