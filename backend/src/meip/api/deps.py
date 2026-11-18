"""Request dependencies: authentication, tenant resolution, unit of work."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Annotated

import structlog
from fastapi import Depends, Header, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from meip.auth.provider import AuthProvider, Principal
from meip.core.config import Settings
from meip.core.errors import ForbiddenError, UnauthorizedError
from meip.tenancy.context import Actor, OperationContext, open_tenant_scope
from meip.tenancy.permissions import Permission
from meip.tenancy.service import resolve_membership

_bearer = HTTPBearer(auto_error=False, description="OIDC access token")


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_auth_provider(request: Request) -> AuthProvider:
    provider: AuthProvider = request.app.state.auth_provider
    return provider


def get_sessionmaker(request: Request) -> async_sessionmaker[AsyncSession]:
    sessionmaker: async_sessionmaker[AsyncSession] = request.app.state.sessionmaker
    return sessionmaker


async def get_principal(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    provider: Annotated[AuthProvider, Depends(get_auth_provider)],
) -> Principal:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise UnauthorizedError("Bearer token required")
    return await provider.authenticate(credentials.credentials)


async def get_request_context(
    principal: Annotated[Principal, Depends(get_principal)],
    sessionmaker: Annotated[async_sessionmaker[AsyncSession], Depends(get_sessionmaker)],
    x_tenant_id: Annotated[
        uuid.UUID, Header(alias="X-Tenant-ID", description="The firm this request acts within")
    ],
) -> AsyncIterator[OperationContext]:
    # Anything not explicitly committed by the endpoint is rolled back by the scope.
    async with open_tenant_scope(sessionmaker, x_tenant_id) as scope:
        membership = await resolve_membership(scope.session, principal)
        if membership is None:
            # Same response whether the tenant does not exist or the user is not a member.
            raise ForbiddenError("You do not have access to this firm")
        structlog.contextvars.bind_contextvars(
            tenant_id=str(x_tenant_id), user_id=str(membership.user_id)
        )
        yield OperationContext(
            scope=scope,
            actor=Actor.user(membership.user_id, membership.membership_id, membership.role),
        )


Context = Annotated[OperationContext, Depends(get_request_context)]
CurrentPrincipal = Annotated[Principal, Depends(get_principal)]


# --- Authorization -----------------------------------------------------------------------
#
# Pattern: every tenant-scoped route declares its required permission in its signature by
# taking one of the ``*Ctx`` aliases below, never the bare ``Context``. Services perform only
# the *conditional* checks that depend on the request data (closing a case requires
# CASE_CLOSE, changing the adviser requires CASE_ASSIGN_ADVISER). The role-by-endpoint
# matrix test pins the resulting behaviour.


def requires(permission: Permission) -> Callable[[OperationContext], Awaitable[OperationContext]]:
    async def check(ctx: Context) -> OperationContext:
        ctx.require(permission)
        return ctx

    check.__name__ = f"requires_{permission.name.lower()}"
    return check


CaseReadCtx = Annotated[OperationContext, Depends(requires(Permission.CASE_READ))]
CaseCreateCtx = Annotated[OperationContext, Depends(requires(Permission.CASE_CREATE))]
CaseUpdateCtx = Annotated[OperationContext, Depends(requires(Permission.CASE_UPDATE))]
CaseTransitionCtx = Annotated[OperationContext, Depends(requires(Permission.CASE_TRANSITION))]
ApplicantWriteCtx = Annotated[OperationContext, Depends(requires(Permission.APPLICANT_WRITE))]
MemberReadCtx = Annotated[OperationContext, Depends(requires(Permission.MEMBER_READ))]
MemberManageCtx = Annotated[OperationContext, Depends(requires(Permission.MEMBER_MANAGE))]
AuditReadCtx = Annotated[OperationContext, Depends(requires(Permission.AUDIT_READ))]
