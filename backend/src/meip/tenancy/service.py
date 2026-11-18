"""Tenancy services: membership resolution, member management, firm onboarding.

Privacy boundary: ``users`` holds identity only. Everything a firm records about a person
(email, display name) lives on that firm's :class:`Membership` row, which row-level security
confines to the firm. Adding a member never reveals anything another firm recorded, and
never grants access until the person accepts the invitation themselves.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from meip.audit import service as audit
from meip.audit.service import AuditAction
from meip.auth.provider import Principal
from meip.core.db import utcnow
from meip.core.errors import BusinessRuleError, ConflictError, NotFoundError, VersionConflictError
from meip.core.ids import uuid7
from meip.core.request_context import request_id_var
from meip.tenancy.context import Actor, OperationContext
from meip.tenancy.models import Membership, MembershipStatus, Role, Tenant, TenantStatus, User
from meip.tenancy.schemas import (
    InvitationOut,
    MemberCreate,
    MemberOut,
    MemberUpdate,
    TenantMembershipOut,
)

_AUDITED_MEMBER_FIELDS = ("role", "status", "email", "display_name")


@dataclass(frozen=True, slots=True)
class ResolvedMembership:
    membership_id: uuid.UUID
    user_id: uuid.UUID
    role: Role


async def resolve_membership(
    session: AsyncSession, principal: Principal
) -> ResolvedMembership | None:
    """Find the principal's active membership in the tenant bound to ``session``."""
    row = (
        await session.execute(
            select(Membership.id, Membership.user_id, Membership.role)
            .join(User, User.id == Membership.user_id)
            .join(Tenant, Tenant.id == Membership.tenant_id)
            .where(
                User.idp_issuer == principal.issuer,
                User.idp_subject == principal.subject,
                Membership.status == MembershipStatus.ACTIVE.value,
                Tenant.status == TenantStatus.ACTIVE.value,
            )
        )
    ).one_or_none()
    if row is None:
        return None
    return ResolvedMembership(membership_id=row[0], user_id=row[1], role=Role(row[2]))


async def memberships_for_principal(
    session: AsyncSession, principal: Principal
) -> tuple[uuid.UUID | None, list[TenantMembershipOut], list[InvitationOut]]:
    """The caller's own active memberships and pending invitations, across firms."""
    rows = (
        (
            await session.execute(
                text("SELECT * FROM app_memberships_for_subject(:issuer, :subject)"),
                {"issuer": principal.issuer, "subject": principal.subject},
            )
        )
        .mappings()
        .all()
    )
    user_id = rows[0]["user_id"] if rows else None
    active = [
        TenantMembershipOut(
            membership_id=r["membership_id"],
            tenant_id=r["tenant_id"],
            tenant_name=r["tenant_name"],
            role=Role(r["role"]),
        )
        for r in rows
        if r["status"] == MembershipStatus.ACTIVE.value
    ]
    invited = [
        InvitationOut(
            membership_id=r["membership_id"],
            tenant_id=r["tenant_id"],
            tenant_name=r["tenant_name"],
            role=Role(r["role"]),
        )
        for r in rows
        if r["status"] == MembershipStatus.INVITED.value
    ]
    return user_id, active, invited


async def accept_invitation(
    session: AsyncSession, principal: Principal, membership_id: uuid.UUID
) -> TenantMembershipOut:
    """Activate a pending invitation addressed to ``principal``. Audited in the database."""
    tenant_id = await session.scalar(
        text(
            "SELECT app_accept_invitation(:membership_id, :issuer, :subject, :audit_id, "
            ":request_id)"
        ),
        {
            "membership_id": membership_id,
            "issuer": principal.issuer,
            "subject": principal.subject,
            "audit_id": uuid7(),
            "request_id": request_id_var.get(),
        },
    )
    if tenant_id is None:
        # Same answer for "no such invitation", "not yours" and "already accepted".
        raise NotFoundError("Invitation not found")
    _, active, _ = await memberships_for_principal(session, principal)
    return next(m for m in active if m.membership_id == membership_id)


def _member_out(membership: Membership) -> MemberOut:
    return MemberOut(
        membership_id=membership.id,
        user_id=membership.user_id,
        email=membership.email,
        display_name=membership.display_name,
        role=Role(membership.role),
        status=MembershipStatus(membership.status),
        accepted_at=membership.accepted_at,
        version=membership.version,
        created_at=membership.created_at,
    )


async def list_members(ctx: OperationContext) -> list[MemberOut]:
    rows = await ctx.session.scalars(
        select(Membership)
        .where(Membership.tenant_id == ctx.tenant_id)
        .order_by(Membership.created_at)
    )
    return [_member_out(m) for m in rows]


async def _get_member(
    ctx: OperationContext, membership_id: uuid.UUID, *, for_update: bool = False
) -> Membership:
    stmt = select(Membership).where(
        Membership.id == membership_id, Membership.tenant_id == ctx.tenant_id
    )
    if for_update:
        stmt = stmt.with_for_update()
    membership: Membership | None = await ctx.session.scalar(stmt)
    if membership is None:
        raise NotFoundError("Member not found")
    return membership


async def add_member(ctx: OperationContext, body: MemberCreate, issuer: str) -> MemberOut:
    """Invite an identity to the firm.

    The response is built solely from what this firm supplied: it is identical whether or
    not the identity already exists or belongs to other firms.
    """
    user_id = await ctx.session.scalar(
        text("SELECT app_register_user(:id, :issuer, :subject)"),
        {"id": uuid7(), "issuer": issuer, "subject": body.idp_subject},
    )
    assert isinstance(user_id, uuid.UUID)  # noqa: S101 - function contract
    existing = await ctx.session.scalar(
        select(Membership.id).where(
            Membership.tenant_id == ctx.tenant_id, Membership.user_id == user_id
        )
    )
    if existing is not None:
        raise ConflictError("This identity already has a membership in the firm")

    membership = Membership(
        tenant_id=ctx.tenant_id,
        user_id=user_id,
        role=body.role.value,
        status=MembershipStatus.INVITED.value,
        email=body.email,
        display_name=body.display_name,
    )
    ctx.session.add(membership)
    await ctx.session.flush()
    await audit.record(
        ctx.session,
        tenant_id=ctx.tenant_id,
        actor=ctx.actor,
        action=AuditAction.MEMBER_ADDED,
        entity_type="membership",
        entity_id=membership.id,
        change_set=audit.creation(
            {"user_id": user_id, **audit.snapshot(membership, _AUDITED_MEMBER_FIELDS)}
        ),
    )
    return _member_out(membership)


async def _lock_active_admins(ctx: OperationContext) -> set[uuid.UUID]:
    """Lock and return the firm's active administrators.

    Taking row locks serialises concurrent admin changes: a second transaction waits, then
    re-evaluates the predicate against committed data, so two simultaneous demotions cannot
    both see "another admin remains".
    """
    rows = await ctx.session.scalars(
        select(Membership.id)
        .where(
            Membership.tenant_id == ctx.tenant_id,
            Membership.role == Role.FIRM_ADMIN.value,
            Membership.status == MembershipStatus.ACTIVE.value,
        )
        .with_for_update()
    )
    return set(rows)


async def update_member(
    ctx: OperationContext, membership_id: uuid.UUID, body: MemberUpdate
) -> MemberOut:
    membership = await _get_member(ctx, membership_id)
    if membership.version != body.version:
        raise VersionConflictError()

    new_role = body.role.value if body.role else membership.role
    new_status = body.status.value if body.status else membership.status

    if new_status != membership.status:
        if new_status == MembershipStatus.INVITED.value:
            raise BusinessRuleError("A membership cannot be returned to 'invited'")
        if new_status == MembershipStatus.ACTIVE.value and membership.accepted_at is None:
            raise BusinessRuleError(
                "Only the invited person can activate a membership, by accepting the invitation"
            )

    removes_admin = (
        membership.role == Role.FIRM_ADMIN.value
        and membership.status == MembershipStatus.ACTIVE.value
        and (new_role != Role.FIRM_ADMIN.value or new_status != MembershipStatus.ACTIVE.value)
    )
    if removes_admin:
        admins = await _lock_active_admins(ctx)
        if membership.id not in admins:
            # Changed concurrently since we read it; the version check below reports it.
            raise VersionConflictError()
        if len(admins) <= 1:
            raise BusinessRuleError("A firm must keep at least one active firm administrator")

    before = audit.snapshot(membership, _AUDITED_MEMBER_FIELDS)
    membership.role = new_role
    membership.status = new_status
    membership.updated_at = utcnow()
    await ctx.session.flush()
    changes = audit.diff(before, audit.snapshot(membership, _AUDITED_MEMBER_FIELDS))
    if not changes:
        raise BusinessRuleError("The update does not change anything")
    await audit.record(
        ctx.session,
        tenant_id=ctx.tenant_id,
        actor=ctx.actor,
        action=AuditAction.MEMBER_UPDATED,
        entity_type="membership",
        entity_id=membership.id,
        change_set=changes,
    )
    return _member_out(membership)


async def get_active_membership(
    session: AsyncSession, tenant_id: uuid.UUID, membership_id: uuid.UUID
) -> Membership | None:
    membership: Membership | None = await session.scalar(
        select(Membership).where(
            Membership.id == membership_id,
            Membership.tenant_id == tenant_id,
            Membership.status == MembershipStatus.ACTIVE.value,
        )
    )
    return membership


# --- Platform operations (run with the owner role, outside any tenant) -----------------


@dataclass(frozen=True, slots=True)
class OnboardedTenant:
    tenant_id: uuid.UUID
    admin_user_id: uuid.UUID
    admin_membership_id: uuid.UUID


async def onboard_tenant(
    owner_session: AsyncSession,
    *,
    name: str,
    fca_frn: str | None,
    admin_issuer: str,
    admin_subject: str,
    admin_email: str | None,
    admin_display_name: str | None,
    operator: str,
) -> OnboardedTenant:
    """Create a firm and its first firm administrator.

    Must be called with a session using the *owner* role: tenant creation is a platform
    operator action and is deliberately not available to the runtime role. The first
    administrator is active immediately: the operator has verified them as part of
    onboarding, which stands in for accepting an invitation. ``operator`` is recorded as
    the audit actor.
    """
    tenant = Tenant(name=name.strip(), fca_frn=fca_frn, status=TenantStatus.ACTIVE.value)
    owner_session.add(tenant)
    await owner_session.flush()

    user_id = await owner_session.scalar(
        text("SELECT app_register_user(:id, :issuer, :subject)"),
        {"id": uuid7(), "issuer": admin_issuer, "subject": admin_subject},
    )
    assert isinstance(user_id, uuid.UUID)  # noqa: S101 - function contract
    membership = Membership(
        tenant_id=tenant.id,
        user_id=user_id,
        role=Role.FIRM_ADMIN.value,
        status=MembershipStatus.ACTIVE.value,
        email=admin_email,
        display_name=admin_display_name,
        accepted_at=utcnow(),
    )
    owner_session.add(membership)
    await owner_session.flush()

    await audit.record(
        owner_session,
        tenant_id=tenant.id,
        actor=Actor.operator(operator),
        action=AuditAction.TENANT_CREATED,
        entity_type="tenant",
        entity_id=tenant.id,
        change_set=audit.creation(
            {"name": tenant.name, "fca_frn": fca_frn, "admin_membership_id": membership.id}
        ),
    )
    return OnboardedTenant(
        tenant_id=tenant.id, admin_user_id=user_id, admin_membership_id=membership.id
    )
