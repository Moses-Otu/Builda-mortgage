from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from meip.api.deps import get_sessionmaker
from meip.core.logging import get_logger

router = APIRouter(tags=["health"])
_log = get_logger("meip.health")


class HealthOut(BaseModel):
    status: Literal["ok", "unavailable"]


@router.get("/health", response_model=HealthOut, summary="Liveness probe")
async def health() -> HealthOut:
    return HealthOut(status="ok")


@router.get(
    "/ready",
    response_model=HealthOut,
    summary="Readiness probe (checks database connectivity)",
    responses={503: {"model": HealthOut}},
)
async def ready(
    sessionmaker: Annotated[async_sessionmaker[AsyncSession], Depends(get_sessionmaker)],
) -> HealthOut | JSONResponse:
    try:
        async with sessionmaker() as session:
            await session.execute(text("SELECT 1"))
    except Exception as exc:
        _log.warning("readiness_failed", exc_type=type(exc).__name__)
        return JSONResponse({"status": "unavailable"}, status_code=503)
    return HealthOut(status="ok")
