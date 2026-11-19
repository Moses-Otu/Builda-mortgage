"""Import every ORM model so ``Base.metadata`` is complete (used by Alembic and tests)."""

from meip.applicants.models import Applicant
from meip.audit.models import AuditEvent, AuditEventDetail
from meip.cases.models import Case
from meip.core.db import Base
from meip.documents.models import (
    Document,
    DocumentVersion,
    ExtractedField,
    ExtractionRun,
    Job,
)
from meip.financial.models import (
    Commitment,
    CommitmentParty,
    ExpenditureItem,
    ExpenditureParty,
    FinancialObservation,
    IncomeSource,
    MeasureDefinition,
    MeasureQualifier,
)
from meip.firm.models import FirmSettings
from meip.tenancy.models import Membership, Tenant, User

__all__ = [
    "Applicant",
    "AuditEvent",
    "AuditEventDetail",
    "Base",
    "Case",
    "Commitment",
    "CommitmentParty",
    "Document",
    "DocumentVersion",
    "ExpenditureItem",
    "ExpenditureParty",
    "ExtractedField",
    "ExtractionRun",
    "FinancialObservation",
    "FirmSettings",
    "IncomeSource",
    "Job",
    "MeasureDefinition",
    "MeasureQualifier",
    "Membership",
    "Tenant",
    "User",
]
