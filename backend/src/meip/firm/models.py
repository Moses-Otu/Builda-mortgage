"""Per-firm settings.

Typed columns rather than a JSON document: there are few settings, each gets a database
constraint, and the catalog guard sees them. Add a column (by migration) per new setting.
Product workflow switches only; lender policy never belongs here.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Integer, SmallInteger
from sqlalchemy.orm import Mapped, mapped_column

from meip.core.db import Base, utcnow

# The platform-wide ceiling; a firm may choose any limit up to it.
PLATFORM_MAX_APPLICANTS = 8
DEFAULT_MAX_APPLICANTS = 4


class FirmSettings(Base):
    __tablename__ = "firm_settings"
    __table_args__ = (
        CheckConstraint(
            f"max_applicants BETWEEN 1 AND {PLATFORM_MAX_APPLICANTS}", name="max_applicants"
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), primary_key=True)
    max_applicants: Mapped[int] = mapped_column(SmallInteger, default=DEFAULT_MAX_APPLICANTS)
    # Stored now, enforced when profile approval arrives (Phase 9): the approving broker must
    # differ from the one who prepared the review.
    require_four_eyes_verification: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
    version: Mapped[int] = mapped_column(Integer, default=1)

    __mapper_args__ = {"version_id_col": version}  # noqa: RUF012
