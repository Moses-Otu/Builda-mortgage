from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from meip.core.schemas import OutputModel


class AuditEventOut(OutputModel):
    id: uuid.UUID
    occurred_at: datetime
    actor_type: Literal["user", "operator", "system"]
    actor_user_id: uuid.UUID | None
    actor_ref: str | None
    action: str
    entity_type: str
    entity_id: uuid.UUID
    case_id: uuid.UUID | None
    request_id: str | None
    changes: dict[str, Any]
