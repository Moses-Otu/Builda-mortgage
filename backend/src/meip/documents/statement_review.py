"""Bank statement confirmation, accounts and holders (Stage 2-B, step 2B-2).

**Confirmation is not validation.** A broker *confirms* that the extracted summary matches the
document (accept, correct with a reason, or reject each field), which account the statement
belongs to, and who holds that account. The statement's *mathematical validation* (totals and
running balance, :func:`meip.documents.statements.integrity`) is computed separately and never
changed by confirming: a broker may confirm a statement whose balances don't add up, and the
discrepancy stays visible. The integrity shown at confirmation is recorded in the audit event.

**Accounts without account numbers.** An account holds the last 4 digits and the keyed
fingerprint of the first statement that carried one. The last 4 digits never decide that two
statements are the same account: the fingerprint suggests and protects, the broker confirms.

Confirming a statement creates **no financial figures**.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select

from meip.applicants.models import Applicant, ApplicantStatus
from meip.audit import service as audit
from meip.audit.service import AuditAction
from meip.cases.service import get_case, require_editable
from meip.core.errors import BusinessRuleError, ConflictError, NotFoundError
from meip.documents.models import (
    BankAccount,
    BankAccountHolder,
    Decision,
    Document,
    DocumentReview,
    DocumentStatus,
    DocumentVersion,
    ExtractedField,
    ExtractedTransaction,
    ExtractionRun,
    FieldReview,
)
from meip.documents.schemas import (
    AccountMatchOut,
    BankAccountOut,
    FieldDecisionIn,
    HolderIn,
    HolderOut,
    IntegrityOut,
    ReviewIn,
)
from meip.documents.statements import Integrity, SimpleRow, integrity
from meip.tenancy.context import OperationContext

DATE_CODES = frozenset({"statement_period_start", "statement_period_end"})
TEXT_CODES = frozenset({"account_ending"})


class AccountConflictError(ConflictError):
    error_type = "account-conflict"
    title = "This statement can't be confirmed to that account"


def kind_of(code: str | None) -> str:
    if code in DATE_CODES:
        return "date"
    if code in TEXT_CODES:
        return "text"
    return "money"


def effective(field: ExtractedField, d: FieldDecisionIn) -> int | date | str | None:
    """The value the broker confirmed: the correction, else the extracted value."""
    if d.decision is Decision.REJECT:
        return None
    if d.decision is Decision.CORRECT:
        for value in (d.corrected_money_minor, d.corrected_date, d.corrected_text):
            if value is not None:
                return value
        return None
    kind = kind_of(field.field_code)
    if kind == "money":
        return field.value_money_minor
    return field.value_date if kind == "date" else field.value_text


def check_decision(field: ExtractedField, d: FieldDecisionIn) -> None:
    where = f"field {field.ordinal}"
    corrected = [
        v for v in (d.corrected_money_minor, d.corrected_date, d.corrected_text) if v is not None
    ]
    reason = (d.reason or "").strip()
    if d.measure is not None or d.qualifier is not None:
        raise BusinessRuleError(f"{where}: a statement confirmation saves no figures")
    if d.decision is Decision.CORRECT:
        kind = kind_of(field.field_code)
        value = {
            "money": d.corrected_money_minor,
            "date": d.corrected_date,
            "text": d.corrected_text,
        }[kind]
        if len(corrected) != 1 or value is None:
            raise BusinessRuleError(f"{where}: a correction needs one {kind} value")
        if field.field_code == "account_ending" and not (
            isinstance(value, str) and len(value) == 4 and value.isdigit()
        ):
            raise BusinessRuleError(f"{where}: the account ending is 4 digits")
        if not reason:
            raise BusinessRuleError(f"{where}: a correction needs a reason")
    elif corrected:
        raise BusinessRuleError(f"{where}: only a correction carries a corrected value")
    if d.decision is Decision.REJECT and not reason:
        raise BusinessRuleError(f"{where}: a rejection needs a reason")


# --- integrity ------------------------------------------------------------------------------------


async def _rows(ctx: OperationContext, run_id: uuid.UUID) -> list[SimpleRow]:
    return [
        SimpleRow(
            t.row_number, t.kind, t.paid_in_minor, t.paid_out_minor, t.balance_minor, t.posted_on
        )
        for t in await ctx.session.scalars(
            select(ExtractedTransaction)
            .where(ExtractedTransaction.run_id == run_id)
            .order_by(ExtractedTransaction.row_number)
        )
    ]


def _integrity(values: dict[str, int | date | str | None], rows: list[SimpleRow]) -> Integrity:
    def money(code: str) -> int | None:
        v = values.get(code)
        return v if isinstance(v, int) else None

    return integrity(
        money("opening_balance"),
        money("closing_balance"),
        rows,
        total_in=money("total_paid_in"),
        total_out=money("total_paid_out"),
    )


def integrity_out(result: Integrity) -> IntegrityOut:
    return IntegrityOut(
        totals=result.totals.value,
        totals_message=result.totals_message,
        running=result.running.value,
        running_message=result.running_message,
        break_row=result.break_row,
    )


async def extracted_integrity(
    ctx: OperationContext, run_id: uuid.UUID, fields: list[ExtractedField]
) -> IntegrityOut:
    """The balance checks on the values as extracted (before any broker correction)."""
    values = {f.field_code: f.value_money_minor for f in fields if f.field_code is not None}
    return integrity_out(_integrity(dict(values), await _rows(ctx, run_id)))


async def confirmed_integrity(
    ctx: OperationContext, review: DocumentReview, fields: list[ExtractedField]
) -> IntegrityOut:
    """The balance checks on the confirmed values (corrections applied, rejections removed)."""
    decisions = {
        r.field_id: r
        for r in await ctx.session.scalars(
            select(FieldReview).where(FieldReview.review_id == review.id)
        )
    }
    values: dict[str, int | date | str | None] = {}
    for f in fields:
        r = decisions.get(f.id)
        if f.field_code is None or r is None or r.decision == Decision.REJECT.value:
            continue
        if r.decision == Decision.CORRECT.value:
            values[f.field_code] = r.corrected_money_minor
        else:
            values[f.field_code] = f.value_money_minor
    return integrity_out(_integrity(values, await _rows(ctx, review.run_id)))


# --- accounts -----------------------------------------------------------------


async def _accounts(ctx: OperationContext, case_id: uuid.UUID) -> list[BankAccount]:
    return list(
        await ctx.session.scalars(
            select(BankAccount)
            .where(BankAccount.case_id == case_id)
            .order_by(BankAccount.created_at, BankAccount.id)
        )
    )


async def account_out(ctx: OperationContext, account: BankAccount) -> BankAccountOut:
    holders = list(
        await ctx.session.scalars(
            select(BankAccountHolder)
            .where(BankAccountHolder.account_id == account.id)
            .order_by(BankAccountHolder.created_at, BankAccountHolder.applicant_id)
        )
    )
    documents = list(
        await ctx.session.scalars(
            select(Document.id)
            .join(DocumentVersion, DocumentVersion.document_id == Document.id)
            .join(ExtractionRun, ExtractionRun.document_version_id == DocumentVersion.id)
            .join(DocumentReview, DocumentReview.run_id == ExtractionRun.id)
            .where(DocumentReview.bank_account_id == account.id)
            .order_by(Document.created_at)
        )
    )
    return BankAccountOut(
        id=account.id,
        account_ending=account.account_ending,
        label=account.label,
        fingerprinted=account.fingerprint is not None,
        joint=len(holders) > 1,
        holders=[HolderOut(applicant_id=h.applicant_id, added_at=h.created_at) for h in holders],
        statement_document_ids=documents,
        created_at=account.created_at,
    )


async def list_accounts(ctx: OperationContext, case_id: uuid.UUID) -> list[BankAccountOut]:
    await get_case(ctx, case_id)
    return [await account_out(ctx, a) for a in await _accounts(ctx, case_id)]


async def account_match(
    ctx: OperationContext, case_id: uuid.UUID, run: ExtractionRun, ending: str | None
) -> AccountMatchOut:
    accounts = await _accounts(ctx, case_id)
    fingerprint = run.account_fingerprint
    match = next((a for a in accounts if fingerprint and a.fingerprint == fingerprint), None)
    same_ending = [
        a.id
        for a in accounts
        if ending and a.account_ending == ending and (match is None or a.id != match.id)
    ]
    if fingerprint is None:
        status = "unverified"
    elif match is not None:
        status = "existing"
    else:
        status = "new"
    return AccountMatchOut(
        status=status,
        account_id=match.id if match else None,
        same_ending_account_ids=same_ending,
    )


async def _active_applicants(ctx: OperationContext, case_id: uuid.UUID) -> set[uuid.UUID]:
    return set(
        await ctx.session.scalars(
            select(Applicant.id).where(
                Applicant.case_id == case_id, Applicant.status == ApplicantStatus.ACTIVE.value
            )
        )
    )


async def add_holder(
    ctx: OperationContext, case_id: uuid.UUID, account_id: uuid.UUID, body: HolderIn
) -> BankAccountOut:
    require_editable(await get_case(ctx, case_id, for_update=True))
    account = await ctx.session.scalar(
        select(BankAccount).where(BankAccount.id == account_id, BankAccount.case_id == case_id)
    )
    if account is None:
        raise NotFoundError("Bank account not found")
    if body.applicant_id not in await _active_applicants(ctx, case_id):
        raise BusinessRuleError("The holder must be an active applicant on this case")
    existing = await ctx.session.scalar(
        select(BankAccountHolder).where(
            BankAccountHolder.account_id == account.id,
            BankAccountHolder.applicant_id == body.applicant_id,
        )
    )
    if existing is not None:
        raise ConflictError("That applicant already holds this account")
    ctx.session.add(
        BankAccountHolder(
            account_id=account.id,
            applicant_id=body.applicant_id,
            tenant_id=ctx.tenant_id,
            case_id=case_id,
            added_by_user_id=ctx.actor.user_id,
        )
    )
    await ctx.session.flush()
    change_set = audit.ChangeSet()
    change_set.note("applicant_id", body.applicant_id)
    await audit.record(
        ctx.session,
        tenant_id=ctx.tenant_id,
        actor=ctx.actor,
        action=AuditAction.BANK_ACCOUNT_HOLDER_ADDED,
        entity_type="bank_account",
        entity_id=account.id,
        case_id=case_id,
        change_set=change_set,
    )
    return await account_out(ctx, account)


# --- confirmation -------------------------------------------------------------


@dataclass(frozen=True)
class Confirmed:
    review: DocumentReview
    account: BankAccount
    account_created: bool
    fingerprint_status: str
    integrity: Integrity


async def confirm_statement(
    ctx: OperationContext,
    case_id: uuid.UUID,
    document: Document,
    run: ExtractionRun,
    fields: list[ExtractedField],
    decisions: dict[int, FieldDecisionIn],
    body: ReviewIn,
) -> Confirmed:
    """Validate everything, then write the confirmation, the account (when new) and its
    holders, the field decisions and one audit event, in the caller's transaction. The caller
    holds the case lock and has checked the document's state, version and decision set."""
    if body.income_source_id is not None or body.frequency is not None:
        raise BusinessRuleError("A statement is confirmed to a bank account, not an income source")
    if (body.bank_account_id is None) == (body.new_account is None):
        raise BusinessRuleError("Give either bank_account_id or new_account")
    for f in fields:
        check_decision(f, decisions[f.ordinal])
    values = {f.field_code: effective(f, decisions[f.ordinal]) for f in fields if f.field_code}
    ending = values.get("account_ending")
    if not isinstance(ending, str):
        raise BusinessRuleError("The account ending is needed: accept or correct that field")

    fingerprint = run.account_fingerprint
    accounts = await _accounts(ctx, case_id)
    owner = next((a for a in accounts if fingerprint and a.fingerprint == fingerprint), None)
    created = False
    if body.bank_account_id is not None:
        account = next((a for a in accounts if a.id == body.bank_account_id), None)
        if account is None:
            raise BusinessRuleError("The bank account is not on this case")
        if account.account_ending != ending:
            raise AccountConflictError(
                f"The statement's account ends {ending}; account {account.id} ends "
                f"{account.account_ending}"
            )
        if fingerprint and account.fingerprint and account.fingerprint != fingerprint:
            raise AccountConflictError(
                f"The statement's account number differs from account {account.id}'s, though "
                "both end the same: they are different accounts"
            )
        if owner is not None and owner.id != account.id:
            raise AccountConflictError(
                f"This statement's account number belongs to account {owner.id}"
            )
    else:
        assert body.new_account is not None  # noqa: S101 # checked above
        if owner is not None:
            raise AccountConflictError(
                f"An account with this statement's account number is already on the case "
                f"({owner.id}): confirm the statement to it",
                extra={"existing_account_id": str(owner.id)},
            )
        holders = list(dict.fromkeys(body.new_account.holder_ids))
        active = await _active_applicants(ctx, case_id)
        if not set(holders) <= active:
            raise BusinessRuleError("Every holder must be an active applicant on this case")
        account = BankAccount(
            tenant_id=ctx.tenant_id,
            case_id=case_id,
            account_ending=ending,
            fingerprint=fingerprint,
            label=(body.new_account.label or "").strip() or None,
            created_by_user_id=ctx.actor.user_id,
        )
        ctx.session.add(account)
        await ctx.session.flush()
        ctx.session.add_all(
            BankAccountHolder(
                account_id=account.id,
                applicant_id=a,
                tenant_id=ctx.tenant_id,
                case_id=case_id,
                added_by_user_id=ctx.actor.user_id,
            )
            for a in holders
        )
        created = True

    result = _integrity(values, await _rows(ctx, run.id))
    review = DocumentReview(
        tenant_id=ctx.tenant_id,
        case_id=case_id,
        run_id=run.id,
        bank_account_id=account.id,
        reviewed_by_user_id=ctx.actor.user_id,
    )
    ctx.session.add(review)
    await ctx.session.flush()
    ctx.session.add_all(
        FieldReview(
            tenant_id=ctx.tenant_id,
            case_id=case_id,
            review_id=review.id,
            field_id=f.id,
            decision=decisions[f.ordinal].decision.value,
            corrected_money_minor=decisions[f.ordinal].corrected_money_minor,
            corrected_date=decisions[f.ordinal].corrected_date,
            corrected_text=decisions[f.ordinal].corrected_text,
            reason=(decisions[f.ordinal].reason or "").strip() or None,
        )
        for f in fields
    )
    before = {"status": document.status}
    document.status = DocumentStatus.REVIEWED.value
    await ctx.session.flush()

    fingerprint_status = (
        "unverified" if fingerprint is None else "matched" if owner is not None else "new"
    )
    holder_count = len(
        list(
            await ctx.session.scalars(
                select(BankAccountHolder.applicant_id).where(
                    BankAccountHolder.account_id == account.id
                )
            )
        )
    )
    change_set = audit.diff(before, {"status": document.status})
    change_set.note("bank_account_id", account.id)
    change_set.note("account_created", created)
    change_set.note("holder_count", holder_count)
    change_set.note("fingerprint_status", fingerprint_status)
    # The validation shown at confirmation, as codes: proves the broker confirmed knowingly.
    change_set.note("integrity_totals", result.totals.value)
    change_set.note("integrity_running", result.running.value)
    for choice in Decision:
        change_set.note(
            f"{choice.value}_count", sum(1 for d in decisions.values() if d.decision is choice)
        )
    change_set.note(
        "corrections",
        [
            {
                "ordinal": f.ordinal,
                "value": str(effective(f, decisions[f.ordinal])),
                "reason": decisions[f.ordinal].reason,
            }
            for f in fields
            if decisions[f.ordinal].decision is Decision.CORRECT
        ]
        or None,
    )
    change_set.note(
        "rejection_reasons",
        [
            {"ordinal": o, "reason": d.reason}
            for o, d in decisions.items()
            if d.decision is Decision.REJECT
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
    return Confirmed(review, account, created, fingerprint_status, result)
