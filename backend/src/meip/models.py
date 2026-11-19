"""Import every ORM model so ``Base.metadata`` is complete (used by Alembic and tests)."""

from meip.applicants.models import Applicant
from meip.audit.models import AuditEvent, AuditEventDetail
from meip.cases.models import Case
from meip.core.db import Base
from meip.documents.models import (
    Document,
    DocumentReview,
    DocumentVersion,
    ExtractedField,
    ExtractedTransaction,
    ExtractionRun,
    FieldReview,
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
from meip.reconciliation.models import CheckAcknowledgement
from meip.tenancy.models import Membership, Tenant, User

__all__ = [
    "Applicant",
    "AuditEvent",
    "AuditEventDetail",
    "Base",
    "Case",
    "CheckAcknowledgement",
    "Commitment",
    "CommitmentParty",
    "Document",
    "DocumentReview",
    "DocumentVersion",
    "ExpenditureItem",
    "ExpenditureParty",
    "ExtractedField",
    "ExtractedTransaction",
    "ExtractionRun",
    "FieldReview",
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
