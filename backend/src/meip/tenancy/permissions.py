"""Role → permission policy. The single place authorisation rules live."""

from __future__ import annotations

from enum import StrEnum

from meip.core.errors import ForbiddenError
from meip.tenancy.models import Role


class Permission(StrEnum):
    CASE_READ = "case:read"
    CASE_CREATE = "case:create"
    CASE_UPDATE = "case:update"
    CASE_TRANSITION = "case:transition"
    CASE_CLOSE = "case:close"
    CASE_ASSIGN_ADVISER = "case:assign_adviser"
    APPLICANT_WRITE = "applicant:write"
    FINANCIAL_WRITE = "financial:write"
    MEMBER_READ = "member:read"
    MEMBER_MANAGE = "member:manage"
    FIRM_SETTINGS_MANAGE = "firm_settings:manage"
    AUDIT_READ = "audit:read"
    # Separate from case permissions so a firm can narrow access to originals later.
    DOCUMENT_READ = "document:read"
    DOCUMENT_WRITE = "document:write"


_READ = {
    Permission.CASE_READ,
    Permission.MEMBER_READ,
    Permission.AUDIT_READ,
    Permission.DOCUMENT_READ,
}
_CASE_WORK = {
    Permission.CASE_CREATE,
    Permission.CASE_UPDATE,
    Permission.CASE_TRANSITION,
    Permission.APPLICANT_WRITE,
    Permission.FINANCIAL_WRITE,
    Permission.DOCUMENT_WRITE,
}

ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.FIRM_ADMIN: frozenset(Permission),
    Role.ADVISER: frozenset(
        _READ | _CASE_WORK | {Permission.CASE_CLOSE, Permission.CASE_ASSIGN_ADVISER}
    ),
    Role.CASE_ADMINISTRATOR: frozenset(_READ | _CASE_WORK),
    Role.READ_ONLY: frozenset(_READ),
}

# Roles that may be assigned as the adviser responsible for a case.
ADVISER_ELIGIBLE_ROLES = frozenset({Role.ADVISER, Role.FIRM_ADMIN})


def has_permission(role: Role, permission: Permission) -> bool:
    return permission in ROLE_PERMISSIONS[role]


def require(role: Role, permission: Permission) -> None:
    if not has_permission(role, permission):
        raise ForbiddenError(f"Your role does not allow '{permission.value}'")
