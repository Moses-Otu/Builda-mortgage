"""Tenants (broker firms), users and memberships."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from meip.core.db import Base, check_in, utcnow
from meip.core.ids import uuid7


class TenantStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"


class Role(StrEnum):
    FIRM_ADMIN = "firm_admin"
    ADVISER = "adviser"
    CASE_ADMINISTRATOR = "case_administrator"
    READ_ONLY = "read_only"


class MembershipStatus(StrEnum):
    INVITED = "invited"
    """Created by a firm administrator; grants no access until the person accepts."""

    ACTIVE = "active"
    DEACTIVATED = "deactivated"


class Tenant(Base):
    __tablename__ = "tenants"
    __table_args__ = (
        CheckConstraint(check_in("status", TenantStatus), name="status"),
        CheckConstraint("fca_frn IS NULL OR fca_frn ~ '^[0-9]{6,7}$'", name="fca_frn_format"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    name: Mapped[str] = mapped_column(String(200))
    fca_frn: Mapped[str | None] = mapped_column(String(7))
    status: Mapped[str] = mapped_column(String(16), default=TenantStatus.ACTIVE.value)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class User(Base):
    """An identity at the identity provider. Global: may belong to several firms.

    Holds identity only, never profile data. Contact details are recorded per firm on
    :class:`Membership`, so no firm can see what another firm recorded about a person.

    Identity key: ``(idp_issuer, idp_subject)``. ``idp_subject`` is the provider's immutable
    object identifier (Entra ``oid``), not the per-application ``sub``. Entra issuers embed
    the directory (tenant) ID, so the pair is unique across directories.
    """

    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("idp_issuer", "idp_subject"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    idp_issuer: Mapped[str] = mapped_column(String(255))
    idp_subject: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Membership(Base):
    """A person's role in one firm, plus the firm's own record of their contact details."""

    __tablename__ = "memberships"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id"),
        UniqueConstraint("tenant_id", "id"),
        CheckConstraint(check_in("role", Role), name="role"),
        CheckConstraint(check_in("status", MembershipStatus), name="status"),
        CheckConstraint(
            "status <> 'active' OR accepted_at IS NOT NULL", name="activation_requires_acceptance"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    role: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default=MembershipStatus.INVITED.value)
    # Contact details as recorded by *this* firm. Never shared with other firms.
    email: Mapped[str | None] = mapped_column(String(320))
    display_name: Mapped[str | None] = mapped_column(String(200))
    accepted_at: Mapped[datetime | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
    version: Mapped[int] = mapped_column(Integer, default=1)

    __mapper_args__ = {"version_id_col": version}  # noqa: RUF012
