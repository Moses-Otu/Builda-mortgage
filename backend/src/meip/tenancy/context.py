from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from meip.tenancy.models import Role
from meip.tenancy.permissions import Permission, require


@dataclass(slots=True)
class RequestContext:
    """An authenticated, tenant-scoped unit of work for one request.

    ``session`` holds an open transaction with row-level security bound to ``tenant_id``.
    Mutating endpoints must call :meth:`commit` explicitly before returning, so a failed
    commit can never be reported to the client as success.
    """

    session: AsyncSession
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    membership_id: uuid.UUID
    role: Role

    def require(self, permission: Permission) -> None:
        require(self.role, permission)

    async def commit(self) -> None:
        await self.session.commit()
