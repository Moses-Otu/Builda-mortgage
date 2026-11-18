"""Who is acting, within which firm, in which transaction.

* :class:`Actor` - the person or system performing an operation. Workers act as a *system*
  actor (optionally on behalf of the user who caused the work); they never impersonate a
  user.
* :class:`TenantScope` - one database transaction bound to one firm for row-level security.
* :class:`OperationContext` - the two together, which is what domain services receive.

HTTP requests build an ``OperationContext`` with a user actor (``api/deps.py``). Background
workers and integrations build one with :func:`open_tenant_scope` and a system or
integration actor. Each entry point checks its primary permission at its own boundary:
routes via ``requires(...)`` dependencies, workers via their job-kind capabilities.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import StrEnum

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from meip.core.db import set_tenant
from meip.core.errors import ForbiddenError
from meip.tenancy.models import Role
from meip.tenancy.permissions import Permission, has_permission


class ActorKind(StrEnum):
    USER = "user"
    OPERATOR = "operator"
    SYSTEM = "system"
    INTEGRATION = "integration"


@dataclass(frozen=True, slots=True)
class Actor:
    kind: ActorKind
    user_id: uuid.UUID | None = None
    membership_id: uuid.UUID | None = None
    role: Role | None = None
    ref: str | None = None
    """Identifies a non-user actor, e.g. ``worker:extraction`` or ``operator:alice``."""
    on_behalf_of_user_id: uuid.UUID | None = None
    """For system/integration actors: the user whose action caused this work."""
    capabilities: frozenset[Permission] = field(default_factory=frozenset)
    """For system/integration actors: an explicit allowlist. Users get theirs from ``role``."""

    def __post_init__(self) -> None:
        if self.kind is ActorKind.USER:
            if self.user_id is None or self.role is None or self.ref is not None:
                raise ValueError("A user actor needs user_id and role, and no ref")
            if self.on_behalf_of_user_id is not None or self.capabilities:
                raise ValueError("A user actor acts for itself, with role-based permissions")
        else:
            if not self.ref or self.user_id is not None or self.role is not None:
                raise ValueError("A non-user actor needs a ref and no user identity")
            if self.kind is ActorKind.OPERATOR and self.on_behalf_of_user_id is not None:
                raise ValueError("An operator actor acts in their own name")

    @classmethod
    def user(cls, user_id: uuid.UUID, membership_id: uuid.UUID, role: Role) -> Actor:
        return cls(ActorKind.USER, user_id=user_id, membership_id=membership_id, role=role)

    @classmethod
    def system(
        cls,
        ref: str,
        capabilities: frozenset[Permission] = frozenset(),
        on_behalf_of_user_id: uuid.UUID | None = None,
    ) -> Actor:
        return cls(
            ActorKind.SYSTEM,
            ref=ref,
            capabilities=capabilities,
            on_behalf_of_user_id=on_behalf_of_user_id,
        )

    @classmethod
    def operator(cls, ref: str) -> Actor:
        return cls(ActorKind.OPERATOR, ref=ref)

    def can(self, permission: Permission) -> bool:
        if self.kind is ActorKind.USER:
            assert self.role is not None  # noqa: S101 - guaranteed by __post_init__
            return has_permission(self.role, permission)
        return permission in self.capabilities

    def require(self, permission: Permission) -> None:
        if not self.can(permission):
            raise ForbiddenError(f"Not permitted to '{permission.value}'")


@dataclass(slots=True)
class TenantScope:
    """An open transaction with row-level security bound to ``tenant_id``."""

    session: AsyncSession
    tenant_id: uuid.UUID

    async def commit(self) -> None:
        await self.session.commit()


@asynccontextmanager
async def open_tenant_scope(
    sessionmaker: async_sessionmaker[AsyncSession], tenant_id: uuid.UUID
) -> AsyncIterator[TenantScope]:
    """Open a tenant-bound transaction. Anything not explicitly committed is rolled back."""
    async with sessionmaker() as session:
        await session.begin()
        await set_tenant(session, tenant_id)
        try:
            yield TenantScope(session=session, tenant_id=tenant_id)
        finally:
            if session.in_transaction():
                await session.rollback()


@dataclass(slots=True)
class OperationContext:
    """What a domain service receives: a tenant-bound transaction and the acting party.

    Mutating callers must call :meth:`commit` explicitly, so a failed commit can never be
    reported as success.
    """

    scope: TenantScope
    actor: Actor
    correlation_id: str | None = None
    """Job ID for background work. HTTP requests are correlated by request ID instead."""

    @property
    def session(self) -> AsyncSession:
        return self.scope.session

    @property
    def tenant_id(self) -> uuid.UUID:
        return self.scope.tenant_id

    def require(self, permission: Permission) -> None:
        self.actor.require(permission)

    async def commit(self) -> None:
        await self.scope.commit()
