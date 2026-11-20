""" "Supported by" for each item (step 3-A2): five kinds of support, kept separate.

Pure: derived from what the profile already holds (figures with provenance, check findings)
plus the reviews that bind a document to an income source. No new stored state.

1. **Declared**: the applicant's declared figures for the item. Never verification.
2. **Documentary**: documents the broker reviewed against the item. Each supports only the
   measures it carries, for the period they cover.
3. **Corroborating**: today only an exact salary credit (``SALARY-CREDIT`` passed). It confirms
   receipt of the net amount, nothing more (:data:`SALARY_CREDIT_SCOPE`).
4. **Findings**: the checks about the item, with acknowledgement status.
5. **Approval**: the broker's approval of the whole profile; profile-level, not here.

**Quantity is not reliability.** Everything is a list; nothing counts, scores or ranks
evidence. A near match, multiple possible credits, no credit, or a credit into an account
the payslip's applicant doesn't hold are findings, never corroboration.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime

from meip.profile.schemas import (
    Corroboration,
    DiscrepancyRef,
    DocumentarySupport,
    FigureOut,
    FindingRef,
    ItemFindings,
    ItemSupport,
    MemberRef,
    NotRunRef,
)
from meip.reconciliation.schemas import FindingOut

# Versioned with the profile rule (profile/1): changing the wording means a new rule version.
SALARY_CREDIT_SCOPE = (
    "Confirms that an amount equal to this payslip's net pay was received on {date} into "
    "account ending {ending}. It does not confirm contractual basic salary, gross pay, the "
    "employer, or that the income will continue."
)


@dataclass(frozen=True)
class ReviewedDocument:
    """A document review bound to an income source (``document_reviews.income_source_id``)."""

    income_source_id: uuid.UUID
    document_id: uuid.UUID
    document_type: str
    file_name: str | None
    reviewed_by: MemberRef | None
    reviewed_at: datetime


def _day(d: date) -> str:
    return f"{d.day} {d:%b %Y}"


def _documentary(
    item_id: uuid.UUID, figures: list[FigureOut], reviews: list[ReviewedDocument]
) -> list[DocumentarySupport]:
    out: list[DocumentarySupport] = []
    for r in reviews:
        if r.income_source_id != item_id:
            continue
        mine = [
            f
            for f in figures
            if f.item_id == item_id
            and f.evidence is not None
            and f.evidence.document_id == r.document_id
        ]
        starts = [f.period_start for f in mine if f.period_start]
        ends = [f.period_end for f in mine if f.period_end]
        out.append(
            DocumentarySupport(
                document_id=r.document_id,
                document_type=r.document_type,
                file_name=r.file_name,
                period_start=min(starts) if starts else None,
                period_end=max(ends) if ends else None,
                measures=sorted({f.measure for f in mine}),
                corrected_measures=sorted({f.measure for f in mine if f.origin == "corrected"}),
                figures_saved=bool(mine),
                reviewed_by=r.reviewed_by,
                reviewed_at=r.reviewed_at,
            )
        )
    out.sort(
        key=lambda d: (d.period_start or d.reviewed_at.date(), d.reviewed_at, str(d.document_id))
    )
    return out


def _corroboration(f: FindingOut) -> Corroboration | None:
    """Only a passed SALARY-CREDIT (exact, unique, into an account the payslip's applicant
    holds) corroborates."""
    rel = f.relationship
    if f.rule_code != "SALARY-CREDIT" or f.status != "passed" or rel is None:
        return None
    if f.match_outcome != "exact" or not rel.applicant_holds_account:
        return None
    payslip = next((e.document_id for e in f.evidence if e.source_kind == "document"), None)
    credit = next((e for e in f.evidence if e.source_kind == "transaction"), None)
    when = credit.value_date if credit else None
    return Corroboration(
        finding_key=f.key,
        payslip_document_id=payslip,
        credit_date=when,
        amount_minor=credit.value_minor if credit else None,
        account_id=rel.account_id,
        account_ending=rel.account_ending,
        account_label=rel.account_label,
        joint=rel.joint,
        holder_ids=rel.holder_ids,
        statement_document_id=rel.document_id,
        row_number=rel.row_number,
        scope=SALARY_CREDIT_SCOPE.format(
            date=_day(when) if when else "the date shown", ending=rel.account_ending
        ),
    )


def _findings(item_id: uuid.UUID, findings: list[FindingOut]) -> ItemFindings:
    mine = [f for f in findings if f.income_source_id == item_id]

    def ref(f: FindingOut) -> dict[str, str]:
        return {
            "key": f.key,
            "ruleset_version": f.ruleset_version,
            "rule_code": f.rule_code,
            "title": f.title,
        }

    return ItemFindings(
        discrepancies=[
            DiscrepancyRef(**ref(f), acknowledgement=f.acknowledgement)
            for f in mine
            if f.status == "discrepancy"
        ],
        not_run=[NotRunRef(**ref(f), reason=f.message) for f in mine if f.status == "not_run"],
        passed=[FindingRef(**ref(f)) for f in mine if f.status == "passed"],
    )


def derive(
    items: list[tuple[str, uuid.UUID]],
    figures: list[FigureOut],
    findings: list[FindingOut],
    reviews: list[ReviewedDocument],
) -> list[ItemSupport]:
    """One entry per item, in the order given."""
    out: list[ItemSupport] = []
    for kind, item_id in items:
        corroborating = [
            c
            for f in findings
            if f.income_source_id == item_id and (c := _corroboration(f)) is not None
        ]
        corroborating.sort(key=lambda c: (c.credit_date or date.min, c.row_number, c.finding_key))
        out.append(
            ItemSupport(
                item_kind=kind,
                item_id=item_id,
                declared=[f.id for f in figures if f.item_id == item_id and f.origin == "declared"],
                documentary=_documentary(item_id, figures, reviews),
                corroborating=corroborating,
                findings=_findings(item_id, findings),
            )
        )
    return out
