"""Build the case's financial profile (rule ``profile/1``) with provenance for every figure.

**What the profile is.** Every observation on the case that is not retired, from items that
are not withdrawn, grouped by item and labelled by origin. There is deliberately no "current
figure" flag and no selection: nothing is chosen, averaged, annualised or summed (decision
S1). To keep a figure out, the broker retires it as entered in error.

**Provenance** comes from links that already exist: a document figure links to the broker's
field decision, which links to the extracted field (printed label and value, page and boxes),
its extraction run and the document version (with the original's SHA-256).

Read-only. The same builder will produce the approval snapshot (Stage 3-B), so what a broker
sees is exactly what gets approved.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from sqlalchemy import select

from meip.applicants.models import Applicant
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
from meip.profile.schemas import (
    FigureEvidence,
    FigureHistory,
    FigureOut,
    MemberRef,
    Origin,
    ProfileApplicant,
    ProfileCase,
    ProfileOut,
)
from meip.reconciliation import service as checks
from meip.tenancy.context import OperationContext
from meip.tenancy.models import Membership

RULES = "profile/1"


def fingerprint(profile: ProfileOut) -> str:
    """SHA-256 over the profile's canonical JSON (sorted keys, no whitespace), without the
    fingerprint itself."""
    body = profile.model_dump(mode="json", exclude={"fingerprint"})
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


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


async def build_profile(ctx: OperationContext, case_id: uuid.UUID) -> ProfileOut:
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

    user_ids = {o.recorded_by_user_id for o in everything if o.recorded_by_user_id}
    user_ids |= {r.reviewed_by_user_id for _, r, *_ in provenance.values() if r.reviewed_by_user_id}
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

    profile = ProfileOut(
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
        documents=await list_documents(ctx, case_id),
        fingerprint="",
    )
    profile.fingerprint = fingerprint(profile)
    return profile
