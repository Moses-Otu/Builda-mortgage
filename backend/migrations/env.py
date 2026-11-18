"""Alembic environment.

Migrations run as the *owner* role (``MEIP_MIGRATIONS_DATABASE_URL``), never the runtime
application role, which has only DML privileges and is subject to row-level security.
"""

from __future__ import annotations

import asyncio
import os

from alembic import context
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from meip.models import Base

target_metadata = Base.metadata


def _url() -> str:
    url = context.config.attributes.get("database_url") or os.environ.get(
        "MEIP_MIGRATIONS_DATABASE_URL"
    )
    if not url:
        raise RuntimeError("MEIP_MIGRATIONS_DATABASE_URL must be set to the owner-role URL")
    return str(url)


def _run(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        transaction_per_migration=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def _run_online() -> None:
    engine = create_async_engine(_url())
    async with engine.connect() as connection:
        await connection.run_sync(_run)
    await engine.dispose()


_supplied: Connection | None = context.config.attributes.get("connection")

if context.is_offline_mode():
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
elif _supplied is not None:
    # Caller (e.g. the test suite) already holds a connection inside its own event loop.
    _run(_supplied)
else:
    asyncio.run(_run_online())
