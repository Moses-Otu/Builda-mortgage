"""Build the case's financial profile (rule ``profile/1``) with provenance for every figure.

**What the profile is.** Every observation on the case that is not retired, from items that
are not withdrawn, grouped by item and labelled by origin. There is deliberately no "current
figure" flag and no selection: nothing is chosen, averaged, annualised or summed (decision
S1). To keep a figure out, the broker retires it as entered in error.

**Provenance** comes from links that already exist: a document figure links to the broker's
field decision, which links to the extracted field (printed label and value, page and boxes),
its extraction run and the document version (with the original's SHA-256).

Read-only. The same builder produces the approval snapshot (Stage 3-B): :func:`snapshot`
returns the content and its canonical text, whose SHA-256 is the fingerprint. What a broker
sees is exactly what gets approved. Readiness and approval status are derived around it and
are never part of the snapshot or the fingerprint (decision V1).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select

from meip.applicants.models import Applicant
from meip.audit.models import AuditEvent
from meip.audit.service import AuditAction
from meip.cases.models import Case, CaseStatus
from meip.cases.service import get_case
from meip.documents import statement_review
from meip.documents.models import (
    Decision,
    Document,
    DocumentReview,
    DocumentVersion,
    ExtractedField,
    ExtractionRun,
    FieldReview,
)
from meip.documents.service import field_out, list_documents
from meip.financial import service as financial
from meip.financial.schemas import ObservationOut
from meip.profile import attestation, support
from meip.profile.models import ProfileApproval
from meip.profile.schemas import (
    ApprovalStatus,
    Attestation,
    Blocker,
    FigureEvidence,
    FigureHistory,
    FigureOut,
    MemberRef,
    Origin,
    ProfileApplicant,
    ProfileCase,
    ProfileOut,
    ProfileSnapshot,
    Readiness,
    Superseded,
)
from meip.profile.schemas import Notice as NoticeOut
from meip.reconciliation import service as checks
from meip.tenancy.context import OperationContext
from meip.tenancy.models import Membership

RULES = "profile/1"


def canonical(snapshot: ProfileSnapshot) -> str:
    """The snapshot's canonical JSON: sorted keys, no whitespace, UTF-8. Stored verbatim as
    an approval's snapshot (decision V2)."""
    body = snapshot.model_dump(mode="json")
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_of(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def fingerprint(snapshot: ProfileSnapshot) -> str:
    """SHA-256 of the canonical JSON. Readiness, approval and the fingerprint itself are not
    part of the snapshot, so they never change it."""
    return sha256_of(canonical(snapshot))


async def _members(ctx: OperationContext, user_ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    if not user_ids:
        return {}
    rows = await ctx.session.scalars(
        select(Membership).where(
            Membership.tenant_id == ctx.tenant_id, Membership.user_id.in_(user_ids)
        )
    )
    return {m.user_id: m.display_name or m.email or "A firm member" for m in rows}


def _ref(user_id: uuid.UUID | None, names: dict[uuid.UUID, str]) -> MemberRef | None:
    if user_id is None:
        return None
    return MemberRef(user_id=user_id, name=names.get(user_id, "A former member"))


def _item(o: ObservationOut) -> tuple[str, uuid.UUID] | None:
    if o.income_source_id is not None:
        return "income_source", o.income_source_id
    if o.commitment_id is not None:
        return "commitment", o.commitment_id
    if o.expenditure_item_id is not None:
        return "expenditure", o.expenditure_item_id
    return None


async def _document_provenance(
    ctx: OperationContext, field_review_ids: list[uuid.UUID]
) -> dict[uuid.UUID, tuple[FieldReview, DocumentReview, ExtractedField, ExtractionRun, Any]]:
    """field_review_id -> (decision, review, extracted field, run, (document, version))."""
    if not field_review_ids:
        return {}
    rows = await ctx.session.execute(
        select(
            FieldReview, DocumentReview, ExtractedField, ExtractionRun, Document, DocumentVersion
        )
        .join(DocumentReview, DocumentReview.id == FieldReview.review_id)
        .join(ExtractedField, ExtractedField.id == FieldReview.field_id)
        .join(ExtractionRun, ExtractionRun.id == DocumentReview.run_id)
        .join(DocumentVersion, DocumentVersion.id == ExtractionRun.document_version_id)
        .join(Document, Document.id == DocumentVersion.document_id)
        .where(FieldReview.id.in_(field_review_ids))
    )
    return {fr.id: (fr, review, f, run, (doc, ver)) for fr, review, f, run, doc, ver in rows}


async def _reviews_by_item(
    ctx: OperationContext, case_id: uuid.UUID, items: set[uuid.UUID]
) -> list[tuple[uuid.UUID, Document, DocumentVersion, DocumentReview]]:
    """Document reviews bound to an income source (``document_reviews.income_source_id``):
    the documents the broker reviewed against each item, including any whose figures were
    all rejected."""
    rows = await ctx.session.execute(
        select(DocumentReview, Document, DocumentVersion)
        .join(ExtractionRun, ExtractionRun.id == DocumentReview.run_id)
        .join(DocumentVersion, DocumentVersion.id == ExtractionRun.document_version_id)
        .join(Document, Document.id == DocumentVersion.document_id)
        .where(DocumentReview.case_id == case_id, DocumentReview.income_source_id.is_not(None))
    )
    return [
        (review.income_source_id, doc, ver, review)
        for review, doc, ver in rows
        if review.income_source_id in items
    ]


@dataclass(frozen=True)
class Built:
    """A built profile: the case row, the snapshot content and its canonical text."""

    case: Case
    snapshot: ProfileSnapshot
    text: str

    @property
    def fingerprint(self) -> str:
        return sha256_of(self.text)


async def snapshot(ctx: OperationContext, case_id: uuid.UUID) -> Built:
    """Build the profile content. Approving calls this with the case row already locked."""
    case = await get_case(ctx, case_id)  # 404 for an unknown case (or another firm's)
    applicants = list(
        await ctx.session.scalars(
            select(Applicant)
            .where(Applicant.case_id == case_id)
            .order_by(Applicant.status, Applicant.display_order, Applicant.created_at)
        )
    )
    sources = await financial.list_income_sources(ctx, case_id)
    commitments = await financial.list_commitments(ctx, case_id)
    expenditure = await financial.list_expenditure(ctx, case_id)
    live_items = {s.id for s in sources} | {c.id for c in commitments}
    live_items |= {e.id for e in expenditure}

    everything = await financial.list_observations(ctx, case_id, include_retired=True)
    by_id = {o.id: o for o in everything}
    current = [o for o in everything if o.retired_at is None]

    provenance = await _document_provenance(
        ctx, [o.field_review_id for o in current if o.field_review_id is not None]
    )
    found = await checks.get_checks(ctx, case_id)
    cited: dict[uuid.UUID, set[str]] = {}
    for finding in found.findings:
        for e in finding.evidence:
            for ref in (e.observation_id, e.field_review_id):
                if ref is not None:
                    cited.setdefault(ref, set()).add(finding.key)

    bound = await _reviews_by_item(ctx, case_id, {s.id for s in sources})
    documents = await list_documents(ctx, case_id)
    user_ids = {o.recorded_by_user_id for o in everything if o.recorded_by_user_id}
    user_ids |= {r.reviewed_by_user_id for _, r, *_ in provenance.values() if r.reviewed_by_user_id}
    user_ids |= {r.reviewed_by_user_id for *_, r in bound if r.reviewed_by_user_id}
    user_ids |= {
        f.acknowledgement.acknowledged_by_user_id
        for f in found.findings
        if f.acknowledgement is not None and f.acknowledgement.acknowledged_by_user_id
    }
    for d in documents:
        user_ids |= {u for u in (d.created_by_user_id, d.original.uploaded_by_user_id) if u}
    names = await _members(ctx, user_ids)

    figures: list[FigureOut] = []
    for o in current:
        item = _item(o)
        if item is None or item[1] not in live_items:
            continue  # a withdrawn item's figures are not part of the profile
        evidence: FigureEvidence | None = None
        origin: Origin
        if o.field_review_id is not None and o.field_review_id in provenance:
            fr, review, f, run, (doc, ver) = provenance[o.field_review_id]
            corrected = fr.decision == Decision.CORRECT.value
            origin = "corrected" if corrected else "extracted"
            evidence = FigureEvidence(
                document_id=doc.id,
                document_version_id=ver.id,
                document_type=doc.document_type,
                file_name=ver.original_filename,
                sha256=ver.sha256,
                field=field_out(f),
                decision="correct" if corrected else "accept",
                correction_reason=fr.reason if corrected else None,
                run_id=run.id,
                provider=run.provider,
                schema_version=run.schema_version,
                reviewed_by=_ref(review.reviewed_by_user_id, names),
                reviewed_at=review.created_at,
            )
        else:
            origin = "declared" if o.source_kind == "declared" else "broker_entry"
        history: list[FigureHistory] = []
        previous = o.supersedes_observation_id
        while previous is not None and previous in by_id:
            p = by_id[previous]
            history.append(
                FigureHistory(
                    observation_id=p.id,
                    value=p.value,
                    source_kind=p.source_kind,
                    recorded_by=_ref(p.recorded_by_user_id, names),
                    recorded_at=p.recorded_at,
                    retired_reason=p.retired_reason,
                )
            )
            previous = p.supersedes_observation_id
        keys = cited.get(o.id, set()) | (
            cited.get(o.field_review_id, set()) if o.field_review_id else set()
        )
        figures.append(
            FigureOut(
                id=o.id,
                item_kind=item[0],
                item_id=item[1],
                measure=o.measure,
                qualifier=o.qualifier,
                value=o.value,
                frequency=o.frequency,
                period_start=o.period_start,
                period_end=o.period_end,
                tax_year=o.tax_year,
                as_of_date=o.as_of_date,
                source_kind=o.source_kind,
                origin=origin,
                recorded_by=_ref(o.recorded_by_user_id, names),
                recorded_at=o.recorded_at,
                evidence=evidence,
                history=history,
                cited_by=sorted(keys),
            )
        )
    figures.sort(
        key=lambda f: (
            f.item_kind,
            str(f.item_id),
            f.measure,
            f.qualifier or "",
            f.period_start or f.as_of_date or f.recorded_at.date(),
            f.period_end or f.as_of_date or f.recorded_at.date(),
            f.recorded_at,
            str(f.id),
        )
    )

    content = ProfileSnapshot(
        rules=RULES,
        case=ProfileCase(
            id=case.id,
            external_reference=case.external_reference,
            purpose=case.purpose,
            status=case.status,
        ),
        applicants=[
            ProfileApplicant(
                id=a.id,
                display_order=a.display_order,
                party_role=a.party_role,
                is_primary=a.is_primary,
                status=a.status,
                first_name=a.first_name,
                middle_names=a.middle_names,
                last_name=a.last_name,
            )
            for a in applicants
        ],
        income_sources=sources,
        commitments=commitments,
        expenditure=expenditure,
        figures=figures,
        bank_accounts=await statement_review.list_accounts(ctx, case_id),
        checks=found,
        documents=documents,
        support=support.derive(
            [("income_source", s.id) for s in sources]
            + [("commitment", c.id) for c in commitments]
            + [("expenditure", e.id) for e in expenditure],
            figures,
            found.findings,
            [
                support.ReviewedDocument(
                    income_source_id=item_id,
                    document_id=doc.id,
                    document_type=doc.document_type,
                    file_name=ver.original_filename,
                    reviewed_by=_ref(review.reviewed_by_user_id, names),
                    reviewed_at=review.created_at,
                )
                for item_id, doc, ver, review in bound
            ],
        ),
        members=sorted(
            (m for u in user_ids if (m := _ref(u, names)) is not None),
            key=lambda m: str(m.user_id),
        ),
    )
    return Built(case=case, snapshot=content, text=canonical(content))


# --- readiness (decision V5: one function for the screen and the approve route) ---------------


def _item_names(content: ProfileSnapshot) -> dict[uuid.UUID, str]:
    people = {a.id: f"{a.first_name} {a.last_name}" for a in content.applicants}
    out: dict[uuid.UUID, str] = {}
    for s in content.income_sources:
        label = s.name or f"{s.kind.replace('_', ' ').capitalize()} income"
        out[s.id] = f"{label} ({people.get(s.applicant_id, 'an applicant')})"
    for c in content.commitments:
        out[c.id] = c.creditor or str(c.kind).replace("_", " ").capitalize()
    for e in content.expenditure:
        out[e.id] = e.description or str(e.category).replace("_", " ").capitalize()
    return out


OUTSTANDING = {"received", "ready", "extracted"}


def readiness(case: Case, content: ProfileSnapshot) -> Readiness:
    """Blockers are genuine outstanding work only. The absence of a position (no commitments,
    an item without figures, declared-only evidence) never blocks; it may be shown as a
    notice, which never blocks either."""
    blockers: list[Blocker] = []
    # Guided journey: no separate "ready for approval" step. Approval is open while the case
    # is being worked on; a verified case must be reopened (new evidence), a closed one too.
    if case.status in {CaseStatus.VERIFIED.value, CaseStatus.CLOSED.value}:
        blockers.append(
            Blocker(
                code="case_not_open",
                message="This case is already verified or is closed: reopen it to approve again.",
            )
        )
    outstanding = [d for d in content.documents if d.status in OUTSTANDING]
    if outstanding:
        blockers.append(
            Blocker(
                code="documents_outstanding",
                message="Some documents are still processing or waiting for review.",
                ids=[str(d.id) for d in outstanding],
            )
        )
    open_keys = [
        f.key
        for f in content.checks.findings
        if f.status == "discrepancy" and f.acknowledgement is None
    ]
    if open_keys:
        blockers.append(
            Blocker(
                code="discrepancies_unacknowledged",
                message="Acknowledge every discrepancy before approving.",
                ids=open_keys,
            )
        )
    items = len(content.income_sources) + len(content.commitments) + len(content.expenditure)
    if items == 0 and not content.documents:
        blockers.append(
            Blocker(code="nothing_recorded", message="Nothing has been recorded on this case yet.")
        )

    notices: list[NoticeOut] = []
    names = _item_names(content)
    with_figures = {f.item_id for f in content.figures}
    for item_id, name in names.items():
        if item_id not in with_figures:
            notices.append(NoticeOut(message=f"{name}: no current figures."))
    if content.figures and all(f.evidence is None for f in content.figures):
        notices.append(
            NoticeOut(
                message="No document has been relied on; every figure is declared or "
                "entered by the broker."
            )
        )
    text = attestation.text_of(attestation.CURRENT) or ""
    return Readiness(
        ready=not blockers,
        blockers=blockers,
        notices=notices,
        attestation=Attestation(version=attestation.CURRENT, text=text),
    )


# --- approval status (derived from the approval rows; never in the snapshot) -------------------


async def superseded_at(ctx: OperationContext, case_id: uuid.UUID) -> dict[str, datetime]:
    """approval id -> when a reopening superseded it (from the case's status audit events)."""
    rows = await ctx.session.execute(
        select(AuditEvent.changes, AuditEvent.occurred_at).where(
            AuditEvent.case_id == case_id,
            AuditEvent.action == AuditAction.CASE_STATUS_CHANGED.value,
        )
    )
    out: dict[str, datetime] = {}
    for changes, when in rows:
        ref = (changes or {}).get("superseded_approval_id")
        if isinstance(ref, str):
            out[ref] = when
    return out


async def approval_status(ctx: OperationContext, case: Case) -> ApprovalStatus:
    rows = list(
        await ctx.session.scalars(
            select(ProfileApproval)
            .where(ProfileApproval.case_id == case.id)
            .order_by(ProfileApproval.version.desc())
        )
    )
    if not rows:
        return ApprovalStatus()
    ended = await superseded_at(ctx, case.id)
    names = await _members(ctx, {r.approved_by_user_id for r in rows if r.approved_by_user_id})
    current = next((r for r in rows if r.id == case.approval_id), None)
    superseded = [
        Superseded(version=r.version, superseded_at=ended.get(str(r.id)))
        for r in rows
        if r.id != case.approval_id
    ]
    if current is None:
        return ApprovalStatus(status="not_approved", superseded=superseded)
    return ApprovalStatus(
        status="approved",
        approval_id=current.id,
        version=current.version,
        approved_by=_ref(current.approved_by_user_id, names),
        approved_at=current.approved_at,
        superseded=superseded,
    )


async def build_profile(ctx: OperationContext, case_id: uuid.UUID) -> ProfileOut:
    built = await snapshot(ctx, case_id)
    return ProfileOut(
        **built.snapshot.model_dump(),
        readiness=readiness(built.case, built.snapshot),
        approval=await approval_status(ctx, built.case),
        fingerprint=built.fingerprint,
    )
