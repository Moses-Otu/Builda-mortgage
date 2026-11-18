from __future__ import annotations

from fastapi import APIRouter, Response

from meip.api.deps import CaseReadCtx, FirmSettingsManageCtx, IfMatch, etag
from meip.firm import service
from meip.firm.schemas import FirmSettingsOut, FirmSettingsUpdate

router = APIRouter(prefix="/api/v1/firm", tags=["firm"])


@router.get("/settings", response_model=FirmSettingsOut, summary="The firm's settings")
async def get_settings(ctx: CaseReadCtx, response: Response) -> FirmSettingsOut:
    out = await service.get_settings(ctx)
    response.headers["ETag"] = etag(out.version)
    return out


@router.patch(
    "/settings",
    response_model=FirmSettingsOut,
    summary="Change the firm's settings",
    description="Firm administrators only. Limits apply to future changes, not existing cases.",
)
async def update_settings(
    body: FirmSettingsUpdate, ctx: FirmSettingsManageCtx, version: IfMatch, response: Response
) -> FirmSettingsOut:
    out = await service.update_settings(ctx, body, version)
    await ctx.commit()
    response.headers["ETag"] = etag(out.version)
    return out
