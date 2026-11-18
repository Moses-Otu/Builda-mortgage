from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import Field, StringConstraints

from meip.core.schemas import InputModel, OutputModel, ShortText
from meip.tenancy.models import MembershipStatus, Role


class TenantMembershipOut(OutputModel):
    membership_id: uuid.UUID
    tenant_id: uuid.UUID
    tenant_name: str
    role: Role


class InvitationOut(OutputModel):
    """A pending invitation addressed to the caller. Grants no access until accepted."""

    membership_id: uuid.UUID
    tenant_id: uuid.UUID
    tenant_name: str
    role: Role


class MeOut(OutputModel):
    user_id: uuid.UUID | None = Field(description="Null until the identity is added to a firm")
    issuer: str
    subject: str
    email: str | None = Field(description="From the caller's own token; not stored")
    display_name: str | None = Field(description="From the caller's own token; not stored")
    memberships: list[TenantMembershipOut] = Field(description="Firms the caller can act in")
    invitations: list[InvitationOut] = Field(description="Pending; accept to gain access")


class MemberOut(OutputModel):
    """A member of the current firm. Contact details are those this firm recorded."""

    membership_id: uuid.UUID
    user_id: uuid.UUID
    email: str | None
    display_name: str | None
    role: Role
    status: MembershipStatus
    accepted_at: datetime | None
    version: int
    created_at: datetime


class MemberCreate(InputModel):
    """Invite an identity to the firm. The person must accept before gaining access."""

    idp_subject: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)
    ] = Field(description="The person's stable identifier at the identity provider (Entra oid)")
    email: (
        Annotated[
            str,
            StringConstraints(strip_whitespace=True, max_length=320, pattern=r"^[^@\s]+@[^@\s]+$"),
        ]
        | None
    ) = Field(default=None, description="Recorded by and visible to this firm only")
    display_name: ShortText | None = Field(
        default=None, description="Recorded by and visible to this firm only"
    )
    role: Role


class MemberUpdate(InputModel):
    """Send the membership's current ETag in ``If-Match``."""

    role: Role | None = None
    status: MembershipStatus | None = Field(
        default=None,
        description=(
            "'deactivated' revokes access or a pending invitation. 'active' is only allowed for "
            "a membership the person has previously accepted."
        ),
    )
