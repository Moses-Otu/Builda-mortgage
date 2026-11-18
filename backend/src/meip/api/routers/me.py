from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from meip.api.deps import CurrentPrincipal, get_sessionmaker
from meip.tenancy.schemas import MeOut, TenantMembershipOut
from meip.tenancy.service import accept_invitation, memberships_for_principal

router = APIRouter(prefix="/api/v1/me", tags=["identity"])

Sessions = Annotated[async_sessionmaker[AsyncSession], Depends(get_sessionmaker)]


@router.get(
    "",
    response_model=MeOut,
    summary="Current identity, its firms and pending invitations",
    description="Does not require X-Tenant-ID. Use it to choose the tenant for later calls.",
)
async def me(principal: CurrentPrincipal, sessionmaker: Sessions) -> MeOut:
    async with sessionmaker() as session:
        user_id, memberships, invitations = await memberships_for_principal(session, principal)
    return MeOut(
        user_id=user_id,
        issuer=principal.issuer,
        subject=principal.subject,
        email=principal.email,
        display_name=principal.display_name,
        memberships=memberships,
        invitations=invitations,
    )


@router.post(
    "/invitations/{membership_id}/accept",
    response_model=TenantMembershipOut,
    summary="Accept an invitation to a firm",
    description=(
        "Only the invited identity can accept. Returns 404 if the invitation does not exist, "
        "is addressed to someone else, or is no longer pending."
    ),
)
async def accept(
    membership_id: uuid.UUID, principal: CurrentPrincipal, sessionmaker: Sessions
) -> TenantMembershipOut:
    async with sessionmaker() as session, session.begin():
        return await accept_invitation(session, principal, membership_id)
