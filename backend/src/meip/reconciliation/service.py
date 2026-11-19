"""Run the checks for a case (``payslip-checks/1`` and ``bank-checks/1``), and record
acknowledgements.

Findings are computed in the request from the case's **current** reviewed data and never
stored. The only write is an acknowledgement, which records that a broker has seen a
discrepancy. It changes no financial, review or document data, resolves nothing and approves
no figure: the finding is still reported as a discrepancy, with the acknowledgement attached.
"""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import select

from meip.applicants.models import Applicant
from meip.audit import service as audit
from meip.audit.service import AuditAction
from meip.cases.service import get_case, require_editable
from meip.core.errors import ConflictError
from meip.documents.models import (
    BankAccount,
    BankAccountHolder,
    Decision,
    Document,
    DocumentReview,
    DocumentVersion,
    ExtractedField,
    ExtractedTransaction,
    ExtractionRun,
    FieldReview,
)
from meip.financial.measures import SourceKind
from meip.financial.models import FinancialObservation, IncomeSource
from meip.reconciliation import bank_rules, rules
from meip.reconciliation.models import CheckAcknowledgement
from meip.reconciliation.schemas import (
    AcknowledgementIn,
    AcknowledgementOut,
    ChecksOut,
    EvidenceOut,
    FindingOut,
    RelationshipOut,
)
from meip.tenancy.context import OperationContext

REVIEWED_CODES = frozenset({"pay_date", "pay_period_start", "pay_period_end", "total_deductions"})
LINE_SECTIONS = frozenset({"payment", "deduction"})


class FindingNotCurrentError(ConflictError):
    error_type = "finding-not-current"
    title = "This finding is not a current discrepancy"


class AlreadyAcknowledgedError(ConflictError):
    error_type = "already-acknowledged"
    title = "This finding has already been acknowledged"


# --- loading the case's current reviewed data ----------------------------------------------


def _effective(field: ExtractedField, review: FieldReview) -> int | date | str | None:
    """The value the broker approved: the correction, else the extracted value."""
    if review.decision == Decision.CORRECT.value:
        for value in (review.corrected_money_minor, review.corrected_date, review.corrected_text):
            if value is not None:
                return value
        return None
    for value in (field.value_money_minor, field.value_date, field.value_text):
        if value is not None:
            return value
    return None


async def _load(
    ctx: OperationContext, case_id: uuid.UUID
) -> tuple[list[rules.Payslip], list[rules.Declared], dict[uuid.UUID, uuid.UUID]]:
    session = ctx.session
    sources = {
        s.id: s.applicant_id
        for s in await session.scalars(
            select(IncomeSource).where(
                IncomeSource.case_id == case_id, IncomeSource.withdrawn_at.is_(None)
            )
        )
    }
    reviews = (
        await session.execute(
            select(DocumentReview, DocumentVersion.id, Document.id)
            .join(ExtractionRun, ExtractionRun.id == DocumentReview.run_id)
            .join(DocumentVersion, DocumentVersion.id == ExtractionRun.document_version_id)
            .join(Document, Document.id == DocumentVersion.document_id)
            .where(DocumentReview.case_id == case_id)
        )
    ).all()
    run_ids = [r.run_id for r, *_ in reviews]
    review_ids = [r.id for r, *_ in reviews]
    fields = (
        {
            f.id: f
            for f in await session.scalars(
                select(ExtractedField).where(ExtractedField.run_id.in_(run_ids))
            )
        }
        if run_ids
        else {}
    )
    field_reviews = (
        list(
            await session.scalars(select(FieldReview).where(FieldReview.review_id.in_(review_ids)))
        )
        if review_ids
        else []
    )
    observations = {
        o.field_review_id: o
        for o in await session.scalars(
            select(FinancialObservation).where(
                FinancialObservation.case_id == case_id,
                FinancialObservation.source_kind == SourceKind.DOCUMENT.value,
                FinancialObservation.retired_at.is_(None),
            )
        )
    }

    by_review: dict[uuid.UUID, list[FieldReview]] = {}
    for fr in field_reviews:
        by_review.setdefault(fr.review_id, []).append(fr)

    payslips: list[rules.Payslip] = []
    for review, version_id, document_id in reviews:
        if review.income_source_id is None or review.frequency is None:
            continue  # a bank statement confirmation, not a payslip review
        if review.income_source_id not in sources:
            continue  # withdrawn income source: its figures are retired too
        figures: list[rules.Figure] = []
        reviewed: list[rules.Reviewed] = []
        labels: list[str] = []
        for fr in sorted(by_review.get(review.id, []), key=lambda r: fields[r.field_id].ordinal):
            field = fields[fr.field_id]
            if field.section in LINE_SECTIONS:
                labels.append(field.label_text)
            if fr.decision == Decision.REJECT.value:
                continue  # rejected fields are never used
            observation = observations.get(fr.id)
            if observation is not None and observation.money_minor is not None:
                figures.append(
                    rules.Figure(
                        observation_id=observation.id,
                        field_review_id=fr.id,
                        ordinal=field.ordinal,
                        label=field.label_text,
                        measure=observation.measure,
                        qualifier=observation.qualifier,
                        amount=observation.money_minor,
                        frequency=observation.frequency,
                        period_start=observation.period_start,
                        period_end=observation.period_end,
                        as_of=observation.as_of_date,
                    )
                )
            if field.field_code in REVIEWED_CODES:
                value = _effective(field, fr)
                if value is not None:
                    reviewed.append(
                        rules.Reviewed(
                            field_review_id=fr.id,
                            ordinal=field.ordinal,
                            label=field.label_text,
                            code=field.field_code,
                            decision=fr.decision,
                            value=value,
                        )
                    )
        payslips.append(
            rules.Payslip(
                document_id=document_id,
                document_version_id=version_id,
                income_source_id=review.income_source_id,
                applicant_id=sources[review.income_source_id],  # the income source's applicant
                frequency=review.frequency,
                figures=tuple(figures),
                reviewed=tuple(reviewed),
                line_labels=tuple(labels),
            )
        )

    declared: dict[uuid.UUID, FinancialObservation] = {}
    for o in await session.scalars(
        select(FinancialObservation)
        .where(
            FinancialObservation.case_id == case_id,
            FinancialObservation.source_kind == SourceKind.DECLARED.value,
            FinancialObservation.measure == "basic_pay",
            FinancialObservation.retired_at.is_(None),
            FinancialObservation.income_source_id.is_not(None),
        )
        .order_by(FinancialObservation.recorded_at, FinancialObservation.id)
    ):
        if o.income_source_id in sources and o.money_minor is not None and o.frequency:
            declared[o.income_source_id] = o  # the most recently recorded current value
    return (
        payslips,
        [
            rules.Declared(o.id, sid, o.measure, o.money_minor or 0, o.frequency or "")
            for sid, o in declared.items()
        ],
        sources,
    )


async def _load_bank(
    ctx: OperationContext, case_id: uuid.UUID
) -> tuple[list[bank_rules.Statement], dict[uuid.UUID, bank_rules.Account], dict[uuid.UUID, str]]:
    """The case's **confirmed** statements (summary values as confirmed, rows as extracted),
    its accounts with their holders, and the applicants' names for the messages."""
    session = ctx.session
    holders: dict[uuid.UUID, list[uuid.UUID]] = {}
    for h in await session.scalars(
        select(BankAccountHolder)
        .where(BankAccountHolder.case_id == case_id)
        .order_by(BankAccountHolder.created_at, BankAccountHolder.applicant_id)
    ):
        holders.setdefault(h.account_id, []).append(h.applicant_id)
    accounts = {
        a.id: bank_rules.Account(
            id=a.id,
            ending=a.account_ending,
            label=a.label,
            holder_ids=tuple(holders.get(a.id, [])),
            fingerprinted=a.fingerprint is not None,
        )
        for a in await session.scalars(select(BankAccount).where(BankAccount.case_id == case_id))
    }
    names = {
        a.id: f"{a.first_name} {a.last_name}"
        for a in await session.scalars(select(Applicant).where(Applicant.case_id == case_id))
    }
    reviews = (
        await session.execute(
            select(DocumentReview, ExtractionRun, DocumentVersion.id, Document)
            .join(ExtractionRun, ExtractionRun.id == DocumentReview.run_id)
            .join(DocumentVersion, DocumentVersion.id == ExtractionRun.document_version_id)
            .join(Document, Document.id == DocumentVersion.document_id)
            .where(DocumentReview.case_id == case_id, DocumentReview.bank_account_id.is_not(None))
        )
    ).all()
    if not reviews:
        return [], accounts, names
    run_ids = [run.id for _, run, *_ in reviews]
    fields = {
        f.id: f
        for f in await session.scalars(
            select(ExtractedField).where(ExtractedField.run_id.in_(run_ids))
        )
    }
    decisions: dict[uuid.UUID, list[FieldReview]] = {}
    for fr in await session.scalars(
        select(FieldReview).where(FieldReview.review_id.in_([r.id for r, *_ in reviews]))
    ):
        decisions.setdefault(fr.review_id, []).append(fr)
    rows: dict[uuid.UUID, list[bank_rules.Txn]] = {}
    for t in await session.scalars(
        select(ExtractedTransaction)
        .where(ExtractedTransaction.run_id.in_(run_ids))
        .order_by(ExtractedTransaction.run_id, ExtractedTransaction.row_number)
    ):
        rows.setdefault(t.run_id, []).append(
            bank_rules.Txn(
                row_number=t.row_number,
                kind=t.kind,
                posted_on=t.posted_on,
                paid_in=t.paid_in_minor,
                paid_out=t.paid_out_minor,
                balance=t.balance_minor,
                description=t.description,
            )
        )
    statements: list[bank_rules.Statement] = []
    for review, run, version_id, document in reviews:
        if review.bank_account_id is None or review.bank_account_id not in accounts:
            continue
        values: list[bank_rules.StatementValue] = []
        for fr in decisions.get(review.id, []):
            f = fields[fr.field_id]
            if fr.decision == Decision.REJECT.value or f.field_code is None:
                continue  # rejected values are never used
            value = _effective(f, fr)
            if value is not None:
                values.append(
                    bank_rules.StatementValue(
                        field_review_id=fr.id,
                        ordinal=f.ordinal,
                        label=f.label_text,
                        code=f.field_code,
                        decision=fr.decision,
                        value=value,
                    )
                )
        statements.append(
            bank_rules.Statement(
                document_id=document.id,
                document_version_id=version_id,
                review_id=review.id,
                account_id=review.bank_account_id,
                filing_applicant_id=document.applicant_id,
                fingerprinted=run.account_fingerprint is not None,
                values=tuple(sorted(values, key=lambda v: v.ordinal)),
                rows=tuple(rows.get(run.id, [])),
            )
        )
    return statements, accounts, names


# --- output -----------------------------------------------------------------------------------


def _evidence(cited: rules.Cited) -> EvidenceOut:
    item, payslip = cited.item, cited.payslip
    if isinstance(item, rules.Declared):
        return EvidenceOut(
            source_kind="declared",
            observation_id=item.observation_id,
            label=f"Declared basic salary ({item.frequency.replace('_', '-')})",
            measure=item.measure,
            value_minor=item.amount,
        )
    common = {
        "source_kind": "document",
        "field_review_id": item.field_review_id,
        "document_id": payslip.document_id if payslip else None,
        "document_label": payslip.name if payslip else None,
        "field_ordinal": item.ordinal,
        "label": item.label,
    }
    if isinstance(item, rules.Figure):
        return EvidenceOut(
            **common,
            observation_id=item.observation_id,
            measure=item.measure,
            value_minor=item.amount,
        )
    value = item.value
    return EvidenceOut(
        **common,
        measure=item.code,
        value_minor=value if isinstance(value, int) else None,
        value_date=value if isinstance(value, date) else None,
        value_text=value if isinstance(value, str) else None,
    )


def _bank_evidence(cited: bank_rules.Cited) -> EvidenceOut:
    item, statement = cited.item, cited.statement
    if isinstance(item, rules.Figure | rules.Reviewed):
        return _evidence(rules.Cited(item, cited.payslip))
    document_id = statement.document_id if statement else None
    document_label = statement.name if statement else None
    if isinstance(item, bank_rules.StatementValue):
        value = item.value
        # One printed "Statement period" label yields two values: say which is which.
        suffix = {"statement_period_start": " (start)", "statement_period_end": " (end)"}
        label = item.label + suffix.get(item.code, "")
        return EvidenceOut(
            source_kind="statement",
            field_review_id=item.field_review_id,
            document_id=document_id,
            document_label=document_label,
            field_ordinal=item.ordinal,
            label=label,
            measure=item.code,
            value_minor=value if isinstance(value, int) else None,
            value_date=value if isinstance(value, date) else None,
            value_text=value if isinstance(value, str) else None,
        )
    when = f", {item.posted_on.day} {item.posted_on:%b %Y}" if item.posted_on else ""
    amount: int | None
    if item.paid_in is not None:
        label, amount = f"Credit{when}", item.paid_in
    elif item.paid_out is not None:
        label, amount = f"Debit{when}", -item.paid_out
    else:
        label, amount = f"Row {item.row_number}{when}", item.balance
    return EvidenceOut(
        source_kind="transaction",
        document_id=document_id,
        document_label=document_label,
        row_number=item.row_number,
        label=label,
        value_minor=amount,
        value_date=item.posted_on,
        value_text=item.description,  # verbatim
    )


def _relationship(rel: bank_rules.Relationship | None) -> RelationshipOut | None:
    if rel is None:
        return None
    return RelationshipOut(
        payslip_applicant_id=rel.payslip_applicant_id,
        account_id=rel.account_id,
        account_ending=rel.account_ending,
        account_label=rel.account_label,
        joint=rel.joint,
        holder_ids=list(rel.holder_ids),
        applicant_holds_account=rel.applicant_holds_account,
        filing_applicant_id=rel.filing_applicant_id,
        document_id=rel.document_id,
        row_number=rel.row_number,
    )


AnyFinding = rules.Finding | bank_rules.Finding


def _ruleset(finding: AnyFinding) -> str:
    if isinstance(finding, bank_rules.Finding):
        return bank_rules.RULESET_VERSION
    return rules.RULESET_VERSION


def _out(finding: AnyFinding, ack: CheckAcknowledgement | None) -> FindingOut:
    bank = finding if isinstance(finding, bank_rules.Finding) else None
    return FindingOut(
        key=finding.key,
        ruleset_version=_ruleset(finding),
        rule_code=finding.rule.value,
        status=finding.status.value,
        title=finding.title,
        message=finding.message,
        income_source_id=finding.income_source_id,
        applicant_id=finding.applicant_id,
        bank_account_id=bank.bank_account_id if bank else None,
        document_id=bank.document_id if bank else None,
        match_outcome=bank.match_outcome.value if bank and bank.match_outcome else None,
        relationship=_relationship(bank.relationship) if bank else None,
        amounts=finding.amounts,
        evidence=(
            [_bank_evidence(c) for c in bank.cited]
            if bank
            else [_evidence(c) for c in finding.cited]  # type: ignore[arg-type]
        ),
        # Only a discrepancy can carry an acknowledgement; a key is unique to its inputs.
        acknowledgement=(
            AcknowledgementOut(
                note=ack.note,
                acknowledged_by_user_id=ack.acknowledged_by_user_id,
                acknowledged_at=ack.created_at,
            )
            if ack is not None and finding.status is rules.Status.DISCREPANCY
            else None
        ),
    )


_ORDER = {rules.Status.DISCREPANCY: 0, rules.Status.NOT_RUN: 1, rules.Status.PASSED: 2}


async def _compute(ctx: OperationContext, case_id: uuid.UUID) -> list[AnyFinding]:
    payslips, declared, sources = await _load(ctx, case_id)
    statements, accounts, names = await _load_bank(ctx, case_id)
    found: list[AnyFinding] = [
        *rules.run(payslips, declared, sources),
        *bank_rules.run(payslips, statements, accounts, names),
    ]
    return sorted(found, key=lambda f: _ORDER[f.status])  # stable: rule order kept within


async def get_checks(ctx: OperationContext, case_id: uuid.UUID) -> ChecksOut:
    await get_case(ctx, case_id)  # 404 for an unknown case (or another firm's)
    found = await _compute(ctx, case_id)
    acks = {
        a.finding_key: a
        for a in await ctx.session.scalars(
            select(CheckAcknowledgement).where(CheckAcknowledgement.case_id == case_id)
        )
    }
    return ChecksOut(
        ruleset_versions=[rules.RULESET_VERSION, bank_rules.RULESET_VERSION],
        parameters={
            rules.RULESET_VERSION: rules.PARAMETERS,
            bank_rules.RULESET_VERSION: bank_rules.PARAMETERS,
        },
        findings=[_out(f, acks.get(f.key)) for f in found],
    )


async def acknowledge(
    ctx: OperationContext, case_id: uuid.UUID, finding_key: str, body: AcknowledgementIn
) -> FindingOut:
    """Record that the broker has seen a **current discrepancy**. Writes the acknowledgement
    and its audit event only; nothing else in the case changes."""
    # The case lock serialises acknowledgements with reviews and figure changes on the case,
    # so the finding can't change between computing it and recording the acknowledgement.
    require_editable(await get_case(ctx, case_id, for_update=True))
    found = {f.key: f for f in await _compute(ctx, case_id)}
    finding = found.get(finding_key)
    if finding is None or finding.status is not rules.Status.DISCREPANCY:
        raise FindingNotCurrentError(
            "No current discrepancy has this key: the figures behind it may have changed. "
            "Reload the checks."
        )
    existing = await ctx.session.scalar(
        select(CheckAcknowledgement).where(
            CheckAcknowledgement.case_id == case_id,
            CheckAcknowledgement.finding_key == finding_key,
        )
    )
    if existing is not None:
        raise AlreadyAcknowledgedError(extra={"acknowledgement_id": str(existing.id)})
    ack = CheckAcknowledgement(
        tenant_id=ctx.tenant_id,
        case_id=case_id,
        finding_key=finding_key,
        rule_code=finding.rule.value,
        ruleset_version=_ruleset(finding),
        note=body.note,
        acknowledged_by_user_id=ctx.actor.user_id,
    )
    ctx.session.add(ack)
    await ctx.session.flush()
    change_set = audit.ChangeSet()
    change_set.note("rule_code", finding.rule.value)
    change_set.note("ruleset_version", _ruleset(finding))
    change_set.note("finding_key", finding_key)
    change_set.note("income_source_id", finding.income_source_id)
    if isinstance(finding, bank_rules.Finding):
        change_set.note("bank_account_id", finding.bank_account_id)
    change_set.note("note", body.note)  # free text: erasable details, never the log
    await audit.record(
        ctx.session,
        tenant_id=ctx.tenant_id,
        actor=ctx.actor,
        action=AuditAction.CHECK_ACKNOWLEDGED,
        entity_type="check_acknowledgement",
        entity_id=ack.id,
        case_id=case_id,
        change_set=change_set,
    )
    return _out(finding, ack)
