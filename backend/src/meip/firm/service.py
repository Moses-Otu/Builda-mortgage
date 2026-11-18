"""Per-firm settings service."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meip.audit import service as audit
from meip.audit.service import AuditAction
from meip.core.errors import BusinessRuleError, NotFoundError, VersionConflictError
from meip.firm.models import FirmSettings
from meip.firm.schemas import FirmSettingsOut, FirmSettingsUpdate
from meip.tenancy.context import OperationContext

_FIELDS = ("max_applicants", "require_four_eyes_verification")


async def _load(session: AsyncSession, tenant_id: uuid.UUID) -> FirmSettings:
    settings: FirmSettings | None = await session.scalar(
        select(FirmSettings).where(FirmSettings.tenant_id == tenant_id)
    )
    if settings is None:  # created at onboarding; absence is a provisioning error
        raise NotFoundError("Firm settings not found")
    return settings


async def max_applicants(ctx: OperationContext) -> int:
    return (await _load(ctx.session, ctx.tenant_id)).max_applicants


async def get_settings(ctx: OperationContext) -> FirmSettingsOut:
    return FirmSettingsOut.model_validate(await _load(ctx.session, ctx.tenant_id))


async def update_settings(
    ctx: OperationContext, body: FirmSettingsUpdate, expected_version: int
) -> FirmSettingsOut:
    settings = await _load(ctx.session, ctx.tenant_id)
    if settings.version != expected_version:
        raise VersionConflictError()
    for field in body.model_fields_set:
        if getattr(body, field) is None:
            raise BusinessRuleError(f"{field} cannot be cleared")
    before = audit.snapshot(settings, _FIELDS)
    for field in body.model_fields_set:
        setattr(settings, field, getattr(body, field))
    changes = audit.diff(before, audit.snapshot(settings, _FIELDS))
    if not changes:
        raise BusinessRuleError("The update does not change anything")
    await ctx.session.flush()
    await audit.record(
        ctx.session,
        tenant_id=ctx.tenant_id,
        actor=ctx.actor,
        action=AuditAction.FIRM_SETTINGS_UPDATED,
        entity_type="firm_settings",
        entity_id=ctx.tenant_id,
        change_set=changes,
    )
    return FirmSettingsOut.model_validate(settings)
