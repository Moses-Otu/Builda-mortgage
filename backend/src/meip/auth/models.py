"""Phase 4 password accounts. Declared for schema drift checks only: the application never
queries this table directly (the runtime role has no privileges on it); it goes through the
``app_local_account`` and ``app_local_account_write`` functions (:mod:`meip.auth.accounts`)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from meip.core.db import Base


class LocalAccount(Base):
    __tablename__ = "local_accounts"
    __table_args__ = (
        CheckConstraint("email = lower(email) AND length(email) BETWEEN 3 AND 320", name="email"),
        CheckConstraint("failed_attempts >= 0", name="failed_attempts"),
        CheckConstraint("password_hash LIKE 'scrypt$%'", name="password_hash"),
    )

    email: Mapped[str] = mapped_column(String(320), primary_key=True)
    password_hash: Mapped[str] = mapped_column(Text)
    must_change_password: Mapped[bool] = mapped_column(Boolean)
    failed_attempts: Mapped[int] = mapped_column(Integer)
    locked_until: Mapped[datetime | None] = mapped_column()
    password_changed_at: Mapped[datetime] = mapped_column()
    created_at: Mapped[datetime] = mapped_column()
