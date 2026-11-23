"""Erase a case completely (Phase 4): run by the platform operator with the owner role.

The running application can't delete case data: its role has no DELETE privileges on these
tables, and immutability triggers refuse deletes for every role. Erasure is therefore a
separate, privileged act (retention brief, section 2): this module runs as the owner role,
which owns the tables, and in **one transaction**:

1. locks the case row;
2. temporarily disables the *user* triggers (immutability, frozen-after-withdrawal and
   supersession checks) on exactly the tables it clears, re-enabling them before commit.
   The table locks this takes keep the application out of those tables for that moment.
   Foreign keys and row-level security are untouched;
3. deletes every row of the case, children before parents, including approvals and their
   snapshots, extracted text and transactions, and acknowledgements;
4. deletes the erasable audit details of the case's events. The audit events themselves
   stay: they are append-only for every role and hold no direct identifiers;
5. records one ``case.erased`` audit event (counts only).

After commit, the stored originals are deleted from blob storage. A blob that can't be deleted
is reported (and found later by ``find-orphan-blobs``), never silently kept.

Downloaded evidence packs and backups are outside the platform's reach (retention brief).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from meip.audit import service as audit
from meip.audit.service import AuditAction
from meip.core.db import utcnow
from meip.tenancy.context import Actor

# Children before parents. Every table with a case_id column must appear (a test checks).
CASE_TABLES: tuple[str, ...] = (
    # Agentic assessment (2026-11-23): children before their assessments.
    "assessment_decisions",
    "evidence_requests",
    "assessment_steps",
    "assessments",
    "check_acknowledgements",
    "profile_approvals",
    "financial_observations",
    "field_reviews",
    "document_reviews",
    "extracted_transactions",
    "extracted_fields",
    "bank_account_holders",
    "bank_accounts",
    "extraction_runs",
    "jobs",
    "document_versions",
    "documents",
    "commitment_parties",
    "expenditure_parties",
    "commitments",
    "expenditure_items",
    "income_sources",
    "applicants",
)
# Tables whose user triggers refuse deletes (immutability, frozen items, supersession).
TRIGGERED: tuple[str, ...] = (
    "assessments",
    "financial_observations",
    "document_versions",
    "commitment_parties",
    "expenditure_parties",
    "commitments",
    "expenditure_items",
    "income_sources",
)


@dataclass
class Erased:
    case_id: uuid.UUID
    tenant_id: uuid.UUID
    rows: dict[str, int] = field(default_factory=dict)
    audit_details: int = 0
    blob_keys: list[str] = field(default_factory=list)
    blobs_not_deleted: list[str] = field(default_factory=list)


async def erase_case(
    session: AsyncSession, tenant_id: uuid.UUID, case_id: uuid.UUID, *, operator: str, reason: str
) -> Erased:
    """Delete one case's data in the caller's transaction (the owner role). Blobs are returned
    for the caller to delete after commit."""
    found = await session.scalar(
        text("SELECT id FROM cases WHERE id = :c AND tenant_id = :t FOR UPDATE"),
        {"c": case_id, "t": tenant_id},
    )
    if found is None:
        raise LookupError("No such case in that firm")
    out = Erased(case_id=case_id, tenant_id=tenant_id)
    out.blob_keys = list(
        (
            await session.execute(
                text("SELECT blob_key FROM document_versions WHERE case_id = :c ORDER BY blob_key"),
                {"c": case_id},
            )
        ).scalars()
    )
    for table in TRIGGERED:
        await session.execute(text(f"ALTER TABLE {table} DISABLE TRIGGER USER"))
    # The case's own pointer to its approval goes first (a CHECK ties it to the status).
    await session.execute(
        text(
            "UPDATE cases SET approval_id = NULL, status = 'closed', closure_reason = "
            "coalesce(closure_reason, 'other') WHERE id = :c"
        ),
        {"c": case_id},
    )
    for table in CASE_TABLES:
        result = await session.execute(
            text(f"DELETE FROM {table} WHERE case_id = :c"),  # noqa: S608 - table from CASE_TABLES
            {"c": case_id},
        )
        out.rows[table] = result.rowcount  # type: ignore[attr-defined]
    for table in TRIGGERED:
        await session.execute(text(f"ALTER TABLE {table} ENABLE TRIGGER USER"))
    details = await session.execute(
        text(
            "DELETE FROM audit_event_details d USING audit_events e "
            "WHERE d.event_id = e.id AND e.case_id = :c"
        ),
        {"c": case_id},
    )
    out.audit_details = details.rowcount  # type: ignore[attr-defined]
    deleted_case = await session.execute(text("DELETE FROM cases WHERE id = :c"), {"c": case_id})
    out.rows["cases"] = deleted_case.rowcount  # type: ignore[attr-defined]

    change_set = audit.ChangeSet()
    change_set.note("rows_deleted", sum(out.rows.values()))
    change_set.note("documents_deleted", len(out.blob_keys))
    change_set.note("note", reason)  # free text: erasable details
    await audit.record(
        session,
        tenant_id=tenant_id,
        actor=Actor.operator(operator),
        action=AuditAction.CASE_ERASED,
        entity_type="case",
        entity_id=case_id,
        case_id=case_id,
        change_set=change_set,
    )
    return out


async def expired_cases(
    session: AsyncSession, days_after_closure: int, now: datetime | None = None
) -> list[tuple[uuid.UUID, uuid.UUID, datetime]]:
    """Closed cases whose closure is older than the retention period: (tenant, case, closed)."""
    cutoff = (now or utcnow()) - timedelta(days=days_after_closure)
    rows = await session.execute(
        text(
            "SELECT tenant_id, id, status_changed_at FROM cases "
            "WHERE status = 'closed' AND status_changed_at < :cutoff ORDER BY status_changed_at"
        ),
        {"cutoff": cutoff},
    )
    return [(r[0], r[1], r[2]) for r in rows]
