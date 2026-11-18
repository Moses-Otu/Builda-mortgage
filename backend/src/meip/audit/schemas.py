from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from meip.core.schemas import OutputModel


class AuditEventOut(OutputModel):
    id: uuid.UUID
    occurred_at: datetime
    actor_type: Literal["user", "operator", "system", "integration"]
    actor_user_id: uuid.UUID | None
    actor_ref: str | None
    on_behalf_of_user_id: uuid.UUID | None
    action: str
    entity_type: str
    entity_id: uuid.UUID
    case_id: uuid.UUID | None
    request_id: str | None
    correlation_id: str | None
    changes: dict[str, Any] = Field(
        description="Permanent record. Personal values appear only as {'changed': true}."
    )
    details: dict[str, Any] | None = Field(
        default=None,
        description="Erasable values for the changes. Null if none, or after retention erasure.",
    )
