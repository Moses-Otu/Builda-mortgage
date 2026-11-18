"""Import every ORM model so ``Base.metadata`` is complete (used by Alembic and tests)."""

from meip.applicants.models import Applicant
from meip.audit.models import AuditEvent, AuditEventDetail
from meip.cases.models import Case
from meip.core.db import Base
from meip.tenancy.models import Membership, Tenant, User

__all__ = [
    "Applicant",
    "AuditEvent",
    "AuditEventDetail",
    "Base",
    "Case",
    "Membership",
    "Tenant",
    "User",
]
