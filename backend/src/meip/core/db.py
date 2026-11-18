"""Database engine, ORM base and tenant-scoped transactions.

Tenant isolation is enforced twice:
1. Application code always filters by the request's tenant.
2. PostgreSQL row-level security: every transaction sets ``app.tenant_id`` via
   ``set_config(..., is_local => true)``; policies compare against it and fail closed
   when it is absent. The runtime role does not own the tables and cannot bypass RLS.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, ClassVar

from sqlalchemy import DateTime, MetaData, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map: ClassVar[dict[Any, Any]] = {datetime: DateTime(timezone=True)}


def utcnow() -> datetime:
    return datetime.now(UTC)


def create_engine(url: str, pool_size: int = 10) -> AsyncEngine:
    return create_async_engine(url, pool_size=pool_size, pool_pre_ping=True)


def create_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    # expire_on_commit=False: responses are built from in-memory objects after commit,
    # because a post-commit refresh would run outside the tenant-scoped transaction.
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=True)


async def set_tenant(session: AsyncSession, tenant_id: uuid.UUID) -> None:
    """Scope the current transaction to ``tenant_id`` for row-level security."""
    await session.execute(
        text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
        {"tenant_id": str(tenant_id)},
    )


def check_in(column: str, enum: type[StrEnum]) -> str:
    """SQL CHECK expression restricting ``column`` to the values of ``enum``."""
    values = ", ".join(f"'{member.value}'" for member in enum)
    return f"{column} IN ({values})"
