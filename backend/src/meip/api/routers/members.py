from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from meip.api.deps import IfMatch, MemberManageCtx, MemberReadCtx, etag, get_auth_provider
from meip.auth.provider import AuthProvider
from meip.tenancy import service
from meip.tenancy.schemas import MemberCreate, MemberOut, MemberUpdate

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
        "Contact details supplied here are visible to this firm only. Firm administrators only."
    ),
)
async def add_member(
    body: MemberCreate,
    ctx: MemberManageCtx,
    provider: Annotated[AuthProvider, Depends(get_auth_provider)],
    response: Response,
) -> MemberOut:
    out = await service.add_member(ctx, body, issuer=provider.issuer)
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
