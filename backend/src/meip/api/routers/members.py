from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from meip.api.deps import (
    IfMatch,
    MemberManageCtx,
    MemberReadCtx,
    etag,
    get_auth_provider,
    get_settings,
)
from meip.auth.provider import AuthProvider
from meip.core.config import AuthMode, Settings
from meip.tenancy import service
from meip.tenancy.schemas import MemberCreate, MemberOut, MemberUpdate, TemporaryPasswordOut

router = APIRouter(prefix="/api/v1/members", tags=["members"])


@router.get("", response_model=list[MemberOut], summary="List the firm's members")
async def list_members(ctx: MemberReadCtx) -> list[MemberOut]:
    return await service.list_members(ctx)


@router.post(
    "",
    response_model=MemberOut,
    status_code=status.HTTP_201_CREATED,
    summary="Invite a person to the firm",
    description=(
        "Creates an *invited* membership for the identity-provider subject. The person gains "
        "access only after accepting via `POST /api/v1/me/invitations/{membership_id}/accept`. "
        "Contact details supplied here are visible to this firm only. Firm administrators only. "
        "Password mode: the subject is the person's email; a new account gets a temporary "
        "password, returned once as `temporary_password`."
    ),
)
async def add_member(
    body: MemberCreate,
    ctx: MemberManageCtx,
    provider: Annotated[AuthProvider, Depends(get_auth_provider)],
    settings: Annotated[Settings, Depends(get_settings)],
    response: Response,
) -> MemberOut:
    out = await service.add_member(
        ctx,
        body,
        issuer=provider.issuer,
        password_accounts=settings.auth_mode is AuthMode.PASSWORD,
    )
    await ctx.commit()
    response.headers["ETag"] = etag(out.version)
    return out


@router.patch(
    "/{membership_id}",
    response_model=MemberOut,
    summary="Change a member's role, or deactivate them",
)
async def update_member(
    membership_id: uuid.UUID,
    body: MemberUpdate,
    ctx: MemberManageCtx,
    version: IfMatch,
    response: Response,
) -> MemberOut:
    out = await service.update_member(ctx, membership_id, body, version)
    await ctx.commit()
    response.headers["ETag"] = etag(out.version)
    return out


@router.post(
    "/{membership_id}/password-reset",
    response_model=TemporaryPasswordOut,
    summary="Give a member a new temporary password",
    description=(
        "Password mode only. Returns a temporary password once; the member must change it at "
        "their next sign-in. Refused (409) for a person who also belongs to another firm. "
        "Firm administrators only. Audited (the password itself is never recorded)."
    ),
    responses={404: {"description": "Member not found, or not password mode"}, 409: {}},
)
async def reset_password(
    membership_id: uuid.UUID,
    ctx: MemberManageCtx,
    provider: Annotated[AuthProvider, Depends(get_auth_provider)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> TemporaryPasswordOut:
    if settings.auth_mode is not AuthMode.PASSWORD:
        from meip.core.errors import NotFoundError

        raise NotFoundError("Not found")
    password = await service.reset_member_password(ctx, membership_id, provider.issuer)
    await ctx.commit()
    return TemporaryPasswordOut(temporary_password=password)
