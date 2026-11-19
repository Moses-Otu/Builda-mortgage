"""Broker review of an extracted payslip: decide every field, correct where needed, and save
the approved financial figures to an existing income source (Phase 2.0, step 2-D).

All or nothing: every check runs before anything is written, and everything is written in
the request's single transaction under the case row lock, so a failure (validation, a
database constraint or the audit write) leaves no partial figures and no reviewed document.
Concurrent or repeated submissions are serialised by that lock, then refused by the status
check (409 with the existing review) and, underneath, by ``UNIQUE (run_id)``.

Four separate records, nothing overwritten: the extracted field (raw text and normalised
value), the broker's decision (with any correction beside the original, and its reason),
the review (income source, frequency, reviewer), and the saved figure (linked to its
decision).
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select

from meip.audit import service as audit
from meip.audit.service import AuditAction
from meip.cases.service import get_case, require_editable
from meip.core.errors import BusinessRuleError, ConflictError, NotFoundError, VersionConflictError
from meip.documents.models import (
    Decision,
    Document,
    DocumentReview,
    DocumentStatus,
    DocumentVersion,
    ExtractedField,
    ExtractionRun,
    FieldReview,
)
from meip.documents.schemas import FieldDecisionIn, FieldDecisionOut, ReviewIn, ReviewOut
from meip.financial import service as financial
from meip.financial.measures import Frequency
from meip.financial.models import FinancialObservation, IncomeSource
from meip.tenancy.context import OperationContext

# Extracted fields that are financial figures, each saved under the measure of the same code.
FIGURE_CODES = frozenset(
    {
        "gross_pay",
        "net_pay",
        "basic_pay",
        "overtime",
        "commission",
        "bonus",
        "allowance",
        "income_tax",
        "national_insurance",
        "pension_contribution",
        "student_loan_deduction",
        "ytd_gross_pay",
        "ytd_income_tax",
    }
)
DATE_CODES = frozenset({"pay_date", "pay_period_start", "pay_period_end"})
TEXT_CODES = frozenset({"pay_frequency", "tax_code"})
QUALIFIED = frozenset({"bonus", "allowance"})
LINE_SECTIONS = frozenset({"payment", "deduction"})

_FREQUENCIES = {
    "weekly": Frequency.WEEKLY,
    "fortnightly": Frequency.FORTNIGHTLY,
    "twoweekly": Frequency.FORTNIGHTLY,
    "2weekly": Frequency.FORTNIGHTLY,
    "fourweekly": Frequency.FOUR_WEEKLY,
    "4weekly": Frequency.FOUR_WEEKLY,
    "monthly": Frequency.MONTHLY,
    "quarterly": Frequency.QUARTERLY,
    "annual": Frequency.ANNUAL,
    "annually": Frequency.ANNUAL,
    "yearly": Frequency.ANNUAL,
}


class AlreadyReviewedError(ConflictError):
    error_type = "already-reviewed"
    title = "This document has already been reviewed"


def frequency_from_text(text: str | None) -> Frequency | None:
    if not text:
        return None
    return _FREQUENCIES.get(re.sub(r"[^a-z0-9]", "", text.lower()))


def _kind(f: ExtractedField) -> str:
    if f.field_code in DATE_CODES:
        return "date"
    if f.field_code in TEXT_CODES:
        return "text"
    return "money"


@dataclass
class _Decided:
    field: ExtractedField
    decision: FieldDecisionIn
    measure: str | None
    qualifier: str | None

    def effective(self) -> int | date | str | None:
        """The value the broker approved: the correction, else the extracted value."""
        d = self.decision
        if d.decision is Decision.REJECT:
            return None
        if d.decision is Decision.CORRECT:
            # Explicit None checks: a corrected amount of 0 is a real value.
            for value in (d.corrected_money_minor, d.corrected_date, d.corrected_text):
                if value is not None:
                    return value
            return None
        f = self.field
        kind = _kind(f)
        if kind == "money":
            return f.value_money_minor
        return f.value_date if kind == "date" else f.value_text


def _check_decision(f: ExtractedField, d: FieldDecisionIn) -> tuple[str | None, str | None]:
    """Validate one decision; return the measure and qualifier it saves under, if any."""
    where = f"field {f.ordinal}"
    corrected = [
        v for v in (d.corrected_money_minor, d.corrected_date, d.corrected_text) if v is not None
    ]
    reason = (d.reason or "").strip()
    if d.decision is Decision.CORRECT:
        expected = {
            "money": d.corrected_money_minor,
            "date": d.corrected_date,
            "text": d.corrected_text,
        }[_kind(f)]
        if len(corrected) != 1 or expected is None:
            raise BusinessRuleError(f"{where}: a correction needs one {_kind(f)} value")
        if not reason:
            raise BusinessRuleError(f"{where}: a correction needs a reason")
    elif corrected:
        raise BusinessRuleError(f"{where}: only a correction carries a corrected value")
    if d.decision is Decision.REJECT:
        if not reason:
            raise BusinessRuleError(f"{where}: a rejection needs a reason")
        if d.measure or d.qualifier:
            raise BusinessRuleError(f"{where}: a rejected field saves nothing")
        return None, None

    if d.measure is not None and (f.field_code is not None or f.section not in LINE_SECTIONS):
        raise BusinessRuleError(
            f"{where}: a measure can only be assigned to an unrecognised payment or deduction line"
        )
    measure = f.field_code if f.field_code in FIGURE_CODES else d.measure
    if measure is None:
        if d.qualifier is not None:
            raise BusinessRuleError(f"{where}: a qualifier needs a measure")
        return None, None  # reviewed evidence only (tax code, dates, uncoded lines, ...)
    qualifier = d.qualifier or ("unspecified" if measure in QUALIFIED else None)
    return measure, qualifier


async def _load(
    ctx: OperationContext, case_id: uuid.UUID, document_id: uuid.UUID
) -> tuple[Document, DocumentVersion, ExtractionRun | None]:
    row = (
        await ctx.session.execute(
            select(Document, DocumentVersion)
            .join(DocumentVersion, DocumentVersion.document_id == Document.id)
            .where(Document.id == document_id, Document.case_id == case_id)
            .with_for_update(of=Document)
        )
    ).one_or_none()
    if row is None:
        raise NotFoundError("Document not found")
    document, version = row
    run = await ctx.session.scalar(
        select(ExtractionRun).where(ExtractionRun.document_version_id == version.id)
    )
    return document, version, run


async def submit_review(
    ctx: OperationContext,
    case_id: uuid.UUID,
    document_id: uuid.UUID,
    body: ReviewIn,
    expected_version: int,
) -> ReviewOut:
    # The case lock serialises every write to the case, including concurrent reviews.
    require_editable(await get_case(ctx, case_id, for_update=True))
    document, _, run = await _load(ctx, case_id, document_id)
    # "Already reviewed" comes first: a duplicate or concurrent submission carries the same,
    # now stale, If-Match, and must learn that the review exists rather than get a 412.
    if document.status == DocumentStatus.REVIEWED.value and run is not None:
        existing = await ctx.session.scalar(
            select(DocumentReview.id).where(DocumentReview.run_id == run.id)
        )
        raise AlreadyReviewedError(
            "Review saved already; correct figures through the financial endpoints",
            extra={"existing_review_id": str(existing)},
        )
    if document.version != expected_version:
        raise VersionConflictError()
    if document.status != DocumentStatus.EXTRACTED.value or run is None:
        raise ConflictError(f"Only an extracted document can be reviewed (it is {document.status})")

    fields = list(
        await ctx.session.scalars(
            select(ExtractedField)
            .where(ExtractedField.run_id == run.id)
            .order_by(ExtractedField.ordinal)
        )
    )

    # --- validate everything before writing anything --------------------------------------
    by_ordinal = {f.ordinal: f for f in fields}
    decisions = {d.ordinal: d for d in body.decisions}
    if len(decisions) != len(body.decisions):
        raise BusinessRuleError("Each field may have only one decision")
    unknown = sorted(set(decisions) - set(by_ordinal))
    missing = sorted(set(by_ordinal) - set(decisions))
    if unknown:
        raise BusinessRuleError(f"Unknown fields: {unknown}")
    if missing:
        raise BusinessRuleError(f"Every field needs a decision; missing: {missing}")
    decided = []
    for ordinal, f in by_ordinal.items():
        measure, qualifier = _check_decision(f, decisions[ordinal])
        decided.append(_Decided(f, decisions[ordinal], measure, qualifier))

    def approved(code: str) -> int | date | str | None:
        return next((d.effective() for d in decided if d.field.field_code == code), None)

    pay_date = approved("pay_date")
    if not isinstance(pay_date, date):
        raise BusinessRuleError("A pay date is needed: accept or correct the pay date field")
    frequency = body.frequency or frequency_from_text(str(approved("pay_frequency") or ""))
    if frequency is None:
        raise BusinessRuleError("The pay frequency is unknown: send 'frequency' with the review")
    start, end = approved("pay_period_start"), approved("pay_period_end")
    period_start = start if isinstance(start, date) else None
    period_end = end if isinstance(end, date) else None

    source = await ctx.session.scalar(
        select(IncomeSource).where(
            IncomeSource.id == body.income_source_id, IncomeSource.case_id == case_id
        )
    )
    if source is None:
        raise BusinessRuleError("The income source is not on this case")
    if document.applicant_id is not None and source.applicant_id != document.applicant_id:
        raise BusinessRuleError("The income source belongs to a different applicant")
    for d in decided:
        if d.measure is not None and not isinstance(d.effective(), int):
            raise BusinessRuleError(
                f"field {d.field.ordinal}: the amount could not be read; correct it or reject it"
            )

    # --- write, all in this transaction -----------------------------------------------------
    review = DocumentReview(
        tenant_id=ctx.tenant_id,
        case_id=case_id,
        run_id=run.id,
        income_source_id=source.id,
        frequency=frequency.value,
        reviewed_by_user_id=ctx.actor.user_id,
    )
    ctx.session.add(review)
    await ctx.session.flush()
    rows = []
    for d in decided:
        rows.append(
            FieldReview(
                tenant_id=ctx.tenant_id,
                case_id=case_id,
                review_id=review.id,
                field_id=d.field.id,
                decision=d.decision.decision.value,
                corrected_money_minor=d.decision.corrected_money_minor,
                corrected_date=d.decision.corrected_date,
                corrected_text=d.decision.corrected_text,
                reason=(d.decision.reason or "").strip() or None,
                measure=d.measure,
                qualifier=d.qualifier,
            )
        )
    ctx.session.add_all(rows)
    await ctx.session.flush()

    figures = [
        financial.DocumentFigure(
            field_review_id=row.id,
            measure=d.measure,
            qualifier=d.qualifier,
            amount_minor=int(d.effective()),  # type: ignore[arg-type] # checked above
            period_start=period_start,
            period_end=period_end,
            pay_date=pay_date,
        )
        for d, row in zip(decided, rows, strict=True)
        if d.measure is not None
    ]
    observations = await financial.record_document_observations(
        ctx, case_id, source.id, figures, frequency
    )

    before = {"status": document.status}
    document.status = DocumentStatus.REVIEWED.value
    await ctx.session.flush()
    counts = {
        f"{choice.value}_count": sum(1 for d in decided if d.decision.decision is choice)
        for choice in Decision
    }
    change_set = audit.diff(before, {"status": document.status})
    change_set.note("income_source_id", source.id)
    change_set.note("observation_ids", sorted(str(o.id) for o in observations))
    for name, value in counts.items():
        change_set.note(name, value)
    # Corrected values and reasons are personal data: erasable details, never the log.
    change_set.note(
        "corrections",
        [
            {
                "ordinal": d.field.ordinal,
                "value": str(d.effective()),
                "reason": d.decision.reason,
            }
            for d in decided
            if d.decision.decision is Decision.CORRECT
        ]
        or None,
    )
    change_set.note(
        "rejection_reasons",
        [
            {"ordinal": d.field.ordinal, "reason": d.decision.reason}
            for d in decided
            if d.decision.decision is Decision.REJECT
        ]
        or None,
    )
    await audit.record(
        ctx.session,
        tenant_id=ctx.tenant_id,
        actor=ctx.actor,
        action=AuditAction.DOCUMENT_REVIEWED,
        entity_type="document",
        entity_id=document.id,
        case_id=case_id,
        change_set=change_set,
    )
    return await _out(ctx, review, fields, document.id)


async def _out(
    ctx: OperationContext,
    review: DocumentReview,
    fields: list[ExtractedField],
    document_id: uuid.UUID,
) -> ReviewOut:
    rows = {
        r.field_id: r
        for r in await ctx.session.scalars(
            select(FieldReview).where(FieldReview.review_id == review.id)
        )
    }
    observations = {
        o.field_review_id: o.id
        for o in await ctx.session.scalars(
            select(FinancialObservation).where(
                FinancialObservation.field_review_id.in_([r.id for r in rows.values()])
            )
        )
    }
    decisions = []
    for f in fields:
        r = rows[f.id]
        decisions.append(
            FieldDecisionOut(
                ordinal=f.ordinal,
                section=f.section,
                field_code=f.field_code,
                label_text=f.label_text,
                raw_text=f.raw_text,
                extracted_money_minor=f.value_money_minor,
                extracted_date=f.value_date,
                extracted_text=f.value_text,
                decision=Decision(r.decision),
                corrected_money_minor=r.corrected_money_minor,
                corrected_date=r.corrected_date,
                corrected_text=r.corrected_text,
                reason=r.reason,
                measure=r.measure,
                qualifier=r.qualifier,
                observation_id=observations.get(r.id),
            )
        )
    return ReviewOut(
        id=review.id,
        document_id=document_id,
        income_source_id=review.income_source_id,
        frequency=Frequency(review.frequency),
        reviewed_by_user_id=review.reviewed_by_user_id,
        created_at=review.created_at,
        decisions=decisions,
    )


async def get_review(
    ctx: OperationContext, case_id: uuid.UUID, document_id: uuid.UUID
) -> ReviewOut:
    row = (
        await ctx.session.execute(
            select(Document, DocumentVersion)
            .join(DocumentVersion, DocumentVersion.document_id == Document.id)
            .where(Document.id == document_id, Document.case_id == case_id)
        )
    ).one_or_none()
    if row is None:
        raise NotFoundError("Document not found")
    document, version = row
    review = await ctx.session.scalar(
        select(DocumentReview)
        .join(ExtractionRun, ExtractionRun.id == DocumentReview.run_id)
        .where(ExtractionRun.document_version_id == version.id)
    )
    if review is None:
        raise NotFoundError("This document has not been reviewed")
    fields = list(
        await ctx.session.scalars(
            select(ExtractedField)
            .where(ExtractedField.run_id == review.run_id)
            .order_by(ExtractedField.ordinal)
        )
    )
    return await _out(ctx, review, fields, document.id)
