"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from meip import __version__
from meip.api.errors import install_error_handlers
from meip.api.middleware import RequestContextMiddleware
from meip.api.routers import applicants, cases, health, me, members
from meip.auth.factory import build_auth_provider
from meip.auth.provider import AuthProvider
from meip.core.config import Settings, get_settings
from meip.core.db import create_engine, create_sessionmaker
from meip.core.logging import configure_logging, get_logger

DESCRIPTION = """
Document intelligence and evidence reconciliation for UK mortgage brokers.

**Phase 1** covers firms, members, mortgage cases, applicants and the audit trail.

All `/api/v1` endpoints except `/me` require an `X-Tenant-ID` header naming the firm the
request acts within. Money is always integer minor units (pence) with an explicit currency.
Figures marked *declared* are borrower statements and are **not verified**.
"""


def create_app(
    settings: Settings | None = None,
    *,
    auth_provider: AuthProvider | None = None,
    sessionmaker: async_sessionmaker[AsyncSession] | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_json)
    log = get_logger("meip.app")

    provider = auth_provider or build_auth_provider(settings)
    engine = None
    if sessionmaker is None:
        engine = create_engine(settings.database_url, settings.database_pool_size)
        sessionmaker = create_sessionmaker(engine)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        log.info("startup", env=settings.env.value, auth_mode=settings.auth_mode.value)
        yield
        if engine is not None:
            await engine.dispose()

    app = FastAPI(
        title="Mortgage Evidence Intelligence Platform API",
        version=__version__,
        description=DESCRIPTION,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.auth_provider = provider
    app.state.sessionmaker = sessionmaker

    app.add_middleware(RequestContextMiddleware)
    install_error_handlers(app)
    for module in (health, me, members, cases, applicants):
        app.include_router(module.router)
    return app
