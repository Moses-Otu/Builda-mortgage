from __future__ import annotations

from datetime import datetime

from pydantic import Field

from meip.core.schemas import InputModel, OutputModel
from meip.firm.models import PLATFORM_MAX_APPLICANTS


class FirmSettingsOut(OutputModel):
    max_applicants: int = Field(description="Most active applicants (borrowers and guarantors)")
    require_four_eyes_verification: bool = Field(
        description="Verification must be approved by a different broker (enforced from Phase 9)"
    )
    platform_max_applicants: int = PLATFORM_MAX_APPLICANTS
    updated_at: datetime
    version: int


class FirmSettingsUpdate(InputModel):
    """Partial update; send the settings' ETag in ``If-Match``."""

    max_applicants: int | None = Field(default=None, ge=1, le=PLATFORM_MAX_APPLICANTS)
    require_four_eyes_verification: bool | None = None
