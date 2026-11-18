"""Financial items and observations: declared and broker-entered data (Phase 1.1).

Rules enforced here (the database enforces types, tenancy and case binding underneath):

* Writes only while the case is editable, holding the case row lock (serialised against
  status transitions and applicant removal).
* New associations need an *active* applicant. Existing ones are never removed or
  reassigned when an applicant leaves: an obligation stays with whoever is liable for it.
* Observations are immutable. A correction is a new observation that supersedes the old
  one; mistakes are retired as ``entered_in_error``. Nothing is deleted.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import date
from typing import Any

from sqlalchemy import delete, select

from meip.applicants.models import Applicant, ApplicantStatus
from meip.audit import service as audit
from meip.audit.service import AuditAction
from meip.cases.service import get_case, require_editable
from meip.core.db import utcnow
from meip.core.errors import BusinessRuleError, ConflictError, NotFoundError, VersionConflictError
from meip.financial.measures import RetiredReason, SourceKind, TemporalKind, ValueType
from meip.financial.models import (
    Commitment,
    CommitmentParty,
    ExpenditureItem,
    ExpenditureParty,
    FinancialObservation,
    IncomeSource,
    MeasureDefinition,
)
from meip.financial.schemas import (
    CommitmentCreate,
    CommitmentOut,
    CommitmentUpdate,
    CountValue,
    DateValue,
    DurationValue,
    ExpenditureCreate,
    ExpenditureOut,
    ExpenditureUpdate,
    IncomeSourceCreate,
    IncomeSourceOut,
    IncomeSourceUpdate,
    MeasureOut,
    MoneyValue,
    ObservationCreate,
    ObservationOut,
    ObservationRetirement,
    ObservationValue,
    PercentValue,
)
from meip.financial.schemas import CommitmentParty as CommitmentPartyIn
from meip.financial.schemas import ExpenditureParty as ExpenditurePartyIn
from meip.tenancy.context import OperationContext


def _enum(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value


async def _editable_case(ctx: OperationContext, case_id: uuid.UUID) -> None:
    case = await get_case(ctx, case_id, for_update=True)
    require_editable(case)


async def _applicants(ctx: OperationContext, case_id: uuid.UUID) -> dict[uuid.UUID, Applicant]:
    rows = await ctx.session.scalars(select(Applicant).where(Applicant.case_id == case_id))
    return {a.id: a for a in rows}


def _require_active(applicants: dict[uuid.UUID, Applicant], applicant_id: uuid.UUID) -> None:
    applicant = applicants.get(applicant_id)
    if applicant is None:
        raise BusinessRuleError("The applicant is not on this case")
    if applicant.status != ApplicantStatus.ACTIVE.value:
        raise ConflictError("The applicant has been removed from the case")


def _check_new_parties(
    applicants: dict[uuid.UUID, Applicant],
    proposed: Iterable[uuid.UUID],
    existing: Iterable[uuid.UUID] = (),
) -> None:
    """New parties must be active applicants; existing ones may stay even if removed."""
    keep = set(existing)
    for applicant_id in proposed:
        if applicant_id not in keep:
            _require_active(applicants, applicant_id)


async def _record(
    ctx: OperationContext,
    action: AuditAction,
    entity_type: str,
    entity_id: uuid.UUID,
    case_id: uuid.UUID,
    change_set: audit.ChangeSet,
) -> None:
    await audit.record(
        ctx.session,
        tenant_id=ctx.tenant_id,
        actor=ctx.actor,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        case_id=case_id,
        change_set=change_set,
    )


# --- measures ----------------------------------------------------------------------------


async def list_measures(ctx: OperationContext) -> list[MeasureOut]:
    rows = await ctx.session.scalars(
        select(MeasureDefinition).order_by(MeasureDefinition.subject_kind, MeasureDefinition.code)
    )
    return [MeasureOut.model_validate(m) for m in rows]


# --- income sources ----------------------------------------------------------------------

_INCOME_FIELDS = (
    "kind",
    "name",
    "basis",
    "is_primary",
    "start_date",
    "end_date",
    "ownership_share",
)


def _income_out(item: IncomeSource) -> IncomeSourceOut:
    return IncomeSourceOut.model_validate(item)


async def list_income_sources(ctx: OperationContext, case_id: uuid.UUID) -> list[IncomeSourceOut]:
    await get_case(ctx, case_id)
    rows = await ctx.session.scalars(
        select(IncomeSource)
        .where(IncomeSource.case_id == case_id)
        .order_by(IncomeSource.created_at)
    )
    return [_income_out(i) for i in rows]


async def _get_income_source(
    ctx: OperationContext, case_id: uuid.UUID, item_id: uuid.UUID
) -> IncomeSource:
    item: IncomeSource | None = await ctx.session.scalar(
        select(IncomeSource).where(IncomeSource.id == item_id, IncomeSource.case_id == case_id)
    )
    if item is None:
        raise NotFoundError("Income source not found")
    return item


async def create_income_source(
    ctx: OperationContext, case_id: uuid.UUID, body: IncomeSourceCreate
) -> IncomeSourceOut:
    await _editable_case(ctx, case_id)
    _require_active(await _applicants(ctx, case_id), body.applicant_id)
    item = IncomeSource(
        tenant_id=ctx.tenant_id,
        case_id=case_id,
        applicant_id=body.applicant_id,
        **{f: _enum(getattr(body, f)) for f in _INCOME_FIELDS},
    )
    _check_dates(item.start_date, item.end_date)
    ctx.session.add(item)
    await ctx.session.flush()
    snapshot = {"applicant_id": item.applicant_id, **audit.snapshot(item, _INCOME_FIELDS)}
    await _record(
        ctx,
        AuditAction.FINANCIAL_ITEM_ADDED,
        "income_source",
        item.id,
        case_id,
        audit.creation(snapshot),
    )
    return _income_out(item)


def _check_dates(start: date | None, end: date | None) -> None:
    if start is not None and end is not None and end < start:
        raise BusinessRuleError("end_date is before start_date")


async def update_income_source(
    ctx: OperationContext,
    case_id: uuid.UUID,
    item_id: uuid.UUID,
    body: IncomeSourceUpdate,
    expected_version: int,
) -> IncomeSourceOut:
    await _editable_case(ctx, case_id)
    item = await _get_income_source(ctx, case_id, item_id)
    if item.version != expected_version:
        raise VersionConflictError()
    _require_active(await _applicants(ctx, case_id), item.applicant_id)
    if "kind" in body.model_fields_set and body.kind is None:
        raise BusinessRuleError("kind cannot be cleared")
    before = audit.snapshot(item, _INCOME_FIELDS)
    for field in body.model_fields_set:
        setattr(item, field, _enum(getattr(body, field)))
    _check_dates(item.start_date, item.end_date)
    changes = audit.diff(before, audit.snapshot(item, _INCOME_FIELDS))
    if not changes:
        raise BusinessRuleError("The update does not change anything")
    await ctx.session.flush()
    await _record(
        ctx, AuditAction.FINANCIAL_ITEM_UPDATED, "income_source", item.id, case_id, changes
    )
    return _income_out(item)


# --- commitments -------------------------------------------------------------------------

_COMMITMENT_FIELDS = (
    "kind",
    "creditor",
    "account_ref_masked",
    "status",
    "to_be_repaid_before_completion",
)


async def _commitment_parties(
    ctx: OperationContext, commitment_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[CommitmentParty]]:
    result: dict[uuid.UUID, list[CommitmentParty]] = {cid: [] for cid in commitment_ids}
    if commitment_ids:
        rows = await ctx.session.scalars(
            select(CommitmentParty)
            .where(CommitmentParty.commitment_id.in_(commitment_ids))
            .order_by(CommitmentParty.created_at, CommitmentParty.applicant_id)
        )
        for party in rows:
            result[party.commitment_id].append(party)
    return result


def _commitment_out(item: Commitment, parties: list[CommitmentParty]) -> CommitmentOut:
    return CommitmentOut(
        id=item.id,
        case_id=item.case_id,
        kind=item.kind,
        creditor=item.creditor,
        account_ref_masked=item.account_ref_masked,
        status=item.status,
        to_be_repaid_before_completion=item.to_be_repaid_before_completion,
        parties=[CommitmentPartyIn.model_validate(p) for p in parties],
        created_at=item.created_at,
        updated_at=item.updated_at,
        version=item.version,
    )


def _parties_snapshot(parties: Iterable[CommitmentPartyIn]) -> list[dict[str, Any]]:
    return [
        {
            "applicant_id": str(p.applicant_id),
            "liability_role": _enum(p.liability_role),
            "responsibility_share": None
            if p.responsibility_share is None
            else str(p.responsibility_share),
        }
        for p in sorted(parties, key=lambda p: str(p.applicant_id))
    ]


async def list_commitments(ctx: OperationContext, case_id: uuid.UUID) -> list[CommitmentOut]:
    await get_case(ctx, case_id)
    items = list(
        await ctx.session.scalars(
            select(Commitment).where(Commitment.case_id == case_id).order_by(Commitment.created_at)
        )
    )
    parties = await _commitment_parties(ctx, [i.id for i in items])
    return [_commitment_out(i, parties[i.id]) for i in items]


def _new_commitment_parties(
    ctx: OperationContext, case_id: uuid.UUID, item_id: uuid.UUID, body: list[CommitmentPartyIn]
) -> list[CommitmentParty]:
    return [
        CommitmentParty(
            commitment_id=item_id,
            applicant_id=p.applicant_id,
            tenant_id=ctx.tenant_id,
            case_id=case_id,
            liability_role=_enum(p.liability_role),
            responsibility_share=p.responsibility_share,
            share_basis=_enum(p.share_basis),
        )
        for p in body
    ]


async def create_commitment(
    ctx: OperationContext, case_id: uuid.UUID, body: CommitmentCreate
) -> CommitmentOut:
    await _editable_case(ctx, case_id)
    _check_new_parties(await _applicants(ctx, case_id), (p.applicant_id for p in body.parties))
    item = Commitment(
        tenant_id=ctx.tenant_id,
        case_id=case_id,
        **{f: _enum(getattr(body, f)) for f in _COMMITMENT_FIELDS},
    )
    ctx.session.add(item)
    await ctx.session.flush()
    parties = _new_commitment_parties(ctx, case_id, item.id, body.parties)
    ctx.session.add_all(parties)
    await ctx.session.flush()
    change_set = audit.creation(audit.snapshot(item, _COMMITMENT_FIELDS)).note(
        "parties", _parties_snapshot(body.parties)
    )
    await _record(ctx, AuditAction.FINANCIAL_ITEM_ADDED, "commitment", item.id, case_id, change_set)
    return _commitment_out(item, parties)


async def update_commitment(
    ctx: OperationContext,
    case_id: uuid.UUID,
    item_id: uuid.UUID,
    body: CommitmentUpdate,
    expected_version: int,
) -> CommitmentOut:
    await _editable_case(ctx, case_id)
    item: Commitment | None = await ctx.session.scalar(
        select(Commitment).where(Commitment.id == item_id, Commitment.case_id == case_id)
    )
    if item is None:
        raise NotFoundError("Commitment not found")
    if item.version != expected_version:
        raise VersionConflictError()
    provided = body.model_fields_set
    for required in ("kind", "status", "parties"):
        if required in provided and getattr(body, required) is None:
            raise BusinessRuleError(f"{required} cannot be cleared")

    current = (await _commitment_parties(ctx, [item.id]))[item.id]
    before = audit.snapshot(item, _COMMITMENT_FIELDS)
    for field in provided - {"parties"}:
        setattr(item, field, _enum(getattr(body, field)))
    changes = audit.diff(before, audit.snapshot(item, _COMMITMENT_FIELDS))

    parties = current
    if body.parties is not None:
        old_snapshot = _parties_snapshot(CommitmentPartyIn.model_validate(p) for p in current)
        new_snapshot = _parties_snapshot(body.parties)
        if old_snapshot != new_snapshot:
            _check_new_parties(
                await _applicants(ctx, case_id),
                (p.applicant_id for p in body.parties),
                existing=(p.applicant_id for p in current),
            )
            await ctx.session.execute(
                delete(CommitmentParty).where(CommitmentParty.commitment_id == item.id)
            )
            parties = _new_commitment_parties(ctx, case_id, item.id, body.parties)
            ctx.session.add_all(parties)
            changes.note("parties", {"from": old_snapshot, "to": new_snapshot})
            # Parties are part of the commitment's state: touch the row so its version (and
            # ETag) changes too.
            item.updated_at = utcnow()

    if not changes:
        raise BusinessRuleError("The update does not change anything")
    await ctx.session.flush()
    await _record(ctx, AuditAction.FINANCIAL_ITEM_UPDATED, "commitment", item.id, case_id, changes)
    return _commitment_out(item, parties)


# --- expenditure -------------------------------------------------------------------------

_EXPENDITURE_FIELDS = ("category", "scope", "description")


async def _expenditure_parties(
    ctx: OperationContext, item_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[ExpenditureParty]]:
    result: dict[uuid.UUID, list[ExpenditureParty]] = {i: [] for i in item_ids}
    if item_ids:
        rows = await ctx.session.scalars(
            select(ExpenditureParty)
            .where(ExpenditureParty.expenditure_item_id.in_(item_ids))
            .order_by(ExpenditureParty.created_at, ExpenditureParty.applicant_id)
        )
        for party in rows:
            result[party.expenditure_item_id].append(party)
    return result


def _expenditure_out(item: ExpenditureItem, parties: list[ExpenditureParty]) -> ExpenditureOut:
    return ExpenditureOut(
        id=item.id,
        case_id=item.case_id,
        category=item.category,
        scope=item.scope,
        description=item.description,
        parties=[ExpenditurePartyIn.model_validate(p) for p in parties],
        created_at=item.created_at,
        updated_at=item.updated_at,
        version=item.version,
    )


def _expenditure_snapshot(parties: Iterable[ExpenditurePartyIn]) -> list[dict[str, Any]]:
    return [
        {"applicant_id": str(p.applicant_id), "share": None if p.share is None else str(p.share)}
        for p in sorted(parties, key=lambda p: str(p.applicant_id))
    ]


async def list_expenditure(ctx: OperationContext, case_id: uuid.UUID) -> list[ExpenditureOut]:
    await get_case(ctx, case_id)
    items = list(
        await ctx.session.scalars(
            select(ExpenditureItem)
            .where(ExpenditureItem.case_id == case_id)
            .order_by(ExpenditureItem.created_at)
        )
    )
    parties = await _expenditure_parties(ctx, [i.id for i in items])
    return [_expenditure_out(i, parties[i.id]) for i in items]


def _new_expenditure_parties(
    ctx: OperationContext, case_id: uuid.UUID, item_id: uuid.UUID, body: list[ExpenditurePartyIn]
) -> list[ExpenditureParty]:
    return [
        ExpenditureParty(
            expenditure_item_id=item_id,
            applicant_id=p.applicant_id,
            tenant_id=ctx.tenant_id,
            case_id=case_id,
            share=p.share,
        )
        for p in body
    ]


async def create_expenditure(
    ctx: OperationContext, case_id: uuid.UUID, body: ExpenditureCreate
) -> ExpenditureOut:
    await _editable_case(ctx, case_id)
    _check_new_parties(await _applicants(ctx, case_id), (p.applicant_id for p in body.parties))
    item = ExpenditureItem(
        tenant_id=ctx.tenant_id,
        case_id=case_id,
        **{f: _enum(getattr(body, f)) for f in _EXPENDITURE_FIELDS},
    )
    ctx.session.add(item)
    await ctx.session.flush()
    parties = _new_expenditure_parties(ctx, case_id, item.id, body.parties)
    ctx.session.add_all(parties)
    await ctx.session.flush()
    change_set = audit.creation(audit.snapshot(item, _EXPENDITURE_FIELDS))
    if body.parties:
        change_set.note("parties", _expenditure_snapshot(body.parties))
    await _record(
        ctx, AuditAction.FINANCIAL_ITEM_ADDED, "expenditure_item", item.id, case_id, change_set
    )
    return _expenditure_out(item, parties)


async def update_expenditure(
    ctx: OperationContext,
    case_id: uuid.UUID,
    item_id: uuid.UUID,
    body: ExpenditureUpdate,
    expected_version: int,
) -> ExpenditureOut:
    await _editable_case(ctx, case_id)
    item: ExpenditureItem | None = await ctx.session.scalar(
        select(ExpenditureItem).where(
            ExpenditureItem.id == item_id, ExpenditureItem.case_id == case_id
        )
    )
    if item is None:
        raise NotFoundError("Expenditure item not found")
    if item.version != expected_version:
        raise VersionConflictError()
    if "category" in body.model_fields_set and body.category is None:
        raise BusinessRuleError("category cannot be cleared")

    current = (await _expenditure_parties(ctx, [item.id]))[item.id]
    before = audit.snapshot(item, _EXPENDITURE_FIELDS)
    for field in body.model_fields_set - {"parties"}:
        setattr(item, field, _enum(getattr(body, field)))
    changes = audit.diff(before, audit.snapshot(item, _EXPENDITURE_FIELDS))

    parties = current
    if body.parties is not None:
        old = _expenditure_snapshot(ExpenditurePartyIn.model_validate(p) for p in current)
        new = _expenditure_snapshot(body.parties)
        if old != new:
            _check_new_parties(
                await _applicants(ctx, case_id),
                (p.applicant_id for p in body.parties),
                existing=(p.applicant_id for p in current),
            )
            await ctx.session.execute(
                delete(ExpenditureParty).where(ExpenditureParty.expenditure_item_id == item.id)
            )
            parties = _new_expenditure_parties(ctx, case_id, item.id, body.parties)
            ctx.session.add_all(parties)
            changes.note("parties", {"from": old, "to": new})
            item.updated_at = utcnow()  # bumps the version, as for commitments

    if not changes:
        raise BusinessRuleError("The update does not change anything")
    await ctx.session.flush()
    await _record(
        ctx, AuditAction.FINANCIAL_ITEM_UPDATED, "expenditure_item", item.id, case_id, changes
    )
    return _expenditure_out(item, parties)


# --- observations ------------------------------------------------------------------------

_VALUE_COLUMNS = (
    "money_minor",
    "currency",
    "percent",
    "duration_months",
    "count_value",
    "date_value",
)


def _value_columns(value: ObservationValue) -> dict[str, Any]:
    columns: dict[str, Any] = dict.fromkeys(_VALUE_COLUMNS)
    match value:
        case MoneyValue():
            columns.update(money_minor=value.amount_minor, currency=value.currency)
        case PercentValue():
            columns.update(percent=value.percent)
        case DurationValue():
            columns.update(duration_months=value.months)
        case CountValue():
            columns.update(count_value=value.count)
        case DateValue():
            columns.update(date_value=value.date)
    return columns


def _value_of(o: FinancialObservation) -> ObservationValue:
    vt = ValueType(o.value_type)
    if vt in (ValueType.MONEY, ValueType.SIGNED_MONEY):
        assert o.money_minor is not None  # noqa: S101 - database CHECK
        return MoneyValue(type=vt.value, amount_minor=o.money_minor)
    if vt in (ValueType.RATE, ValueType.PROPORTION):
        assert o.percent is not None  # noqa: S101
        return PercentValue(type=vt.value, percent=o.percent)
    if vt is ValueType.DURATION:
        assert o.duration_months is not None  # noqa: S101
        return DurationValue(type="duration", months=o.duration_months)
    if vt is ValueType.COUNT:
        assert o.count_value is not None  # noqa: S101
        return CountValue(type="count", count=o.count_value)
    assert o.date_value is not None  # noqa: S101
    return DateValue(type="date", date=o.date_value)


def _observation_out(o: FinancialObservation) -> ObservationOut:
    return ObservationOut(
        id=o.id,
        case_id=o.case_id,
        subject_kind=o.subject_kind,
        income_source_id=o.income_source_id,
        commitment_id=o.commitment_id,
        expenditure_item_id=o.expenditure_item_id,
        measure=o.measure,
        value=_value_of(o),
        frequency=o.frequency,
        period_start=o.period_start,
        period_end=o.period_end,
        tax_year=o.tax_year,
        as_of_date=o.as_of_date,
        source_kind=o.source_kind,
        recorded_by_user_id=o.recorded_by_user_id,
        recorded_at=o.recorded_at,
        supersedes_observation_id=o.supersedes_observation_id,
        retired_at=o.retired_at,
        retired_reason=o.retired_reason,
    )


async def list_observations(
    ctx: OperationContext,
    case_id: uuid.UUID,
    *,
    subject_id: uuid.UUID | None = None,
    include_retired: bool = False,
) -> list[ObservationOut]:
    await get_case(ctx, case_id)
    stmt = select(FinancialObservation).where(FinancialObservation.case_id == case_id)
    if subject_id is not None:
        stmt = stmt.where(
            (FinancialObservation.income_source_id == subject_id)
            | (FinancialObservation.commitment_id == subject_id)
            | (FinancialObservation.expenditure_item_id == subject_id)
        )
    if not include_retired:
        stmt = stmt.where(FinancialObservation.retired_at.is_(None))
    rows = await ctx.session.scalars(
        stmt.order_by(FinancialObservation.recorded_at, FinancialObservation.id)
    )
    return [_observation_out(o) for o in rows]


async def _check_subject(
    ctx: OperationContext, case_id: uuid.UUID, body: ObservationCreate
) -> None:
    """The item must exist in this case; income needs its applicant still on the case."""
    if body.income_source_id is not None:
        source = await _get_income_source(ctx, case_id, body.income_source_id)
        _require_active(await _applicants(ctx, case_id), source.applicant_id)
    elif body.commitment_id is not None:
        found = await ctx.session.scalar(
            select(Commitment.id).where(
                Commitment.id == body.commitment_id, Commitment.case_id == case_id
            )
        )
        if found is None:
            raise NotFoundError("Commitment not found")
    else:
        found = await ctx.session.scalar(
            select(ExpenditureItem.id).where(
                ExpenditureItem.id == body.expenditure_item_id,
                ExpenditureItem.case_id == case_id,
            )
        )
        if found is None:
            raise NotFoundError("Expenditure item not found")


def _subject_id(o: FinancialObservation | ObservationCreate) -> uuid.UUID | None:
    return o.income_source_id or o.commitment_id or o.expenditure_item_id


def _period_key(o: FinancialObservation) -> tuple[Any, ...]:
    return (o.frequency, o.period_start, o.period_end, o.tax_year, o.as_of_date)


async def _superseded(
    ctx: OperationContext, case_id: uuid.UUID, body: ObservationCreate
) -> FinancialObservation | None:
    """The observation a correction replaces: same item, measure and source, still active.

    Source must match so a correction never moves a value between layers: a broker entry
    cannot retire what the borrower declared (the declared-versus-evidenced comparison
    depends on both surviving). The period is checked once the new value's period is known.
    """
    if body.supersedes_observation_id is None:
        return None
    superseded: FinancialObservation | None = await ctx.session.scalar(
        select(FinancialObservation).where(
            FinancialObservation.id == body.supersedes_observation_id,
            FinancialObservation.case_id == case_id,
        )
    )
    if superseded is None:
        raise NotFoundError("The observation to supersede was not found")
    if superseded.retired_at is not None:
        raise ConflictError("That observation has already been retired")
    if _subject_id(superseded) != _subject_id(body) or superseded.measure != body.measure:
        raise BusinessRuleError("A correction must be for the same item and measure")
    if superseded.source_kind != SourceKind(body.source_kind).value:
        raise BusinessRuleError(
            "A correction must come from the same source (declared, broker entry, ...); "
            "record a new observation instead"
        )
    return superseded


async def record_observation(
    ctx: OperationContext, case_id: uuid.UUID, body: ObservationCreate
) -> ObservationOut:
    await _editable_case(ctx, case_id)
    await _check_subject(ctx, case_id, body)

    subject_kind = body.subject_kind
    definition: MeasureDefinition | None = await ctx.session.scalar(
        select(MeasureDefinition).where(
            MeasureDefinition.code == body.measure,
            MeasureDefinition.subject_kind == subject_kind.value,
        )
    )
    if definition is None:
        raise BusinessRuleError(f"'{body.measure}' is not a measure for a {subject_kind.value}")
    if body.value.type != definition.value_type:
        raise BusinessRuleError(
            f"'{body.measure}' takes a {definition.value_type} value, not {body.value.type}"
        )

    superseded = await _superseded(ctx, case_id, body)

    temporal = TemporalKind(definition.temporal_kind)
    as_of = body.as_of_date
    if temporal is TemporalKind.FLOW:
        if body.frequency is None:
            raise BusinessRuleError(f"'{body.measure}' is an amount per period: set frequency")
        if as_of is not None:
            raise BusinessRuleError("as_of_date applies to point-in-time measures only")
    elif temporal is TemporalKind.STOCK:
        if body.frequency is not None:
            raise BusinessRuleError(f"'{body.measure}' is a point-in-time value: no frequency")
        if as_of is None:
            # A correction keeps the original as-of date; a new value defaults to today.
            as_of = superseded.as_of_date if superseded else date.today()  # noqa: DTZ011
    elif any(
        v is not None
        for v in (body.frequency, as_of, body.period_start, body.period_end, body.tax_year)
    ):
        raise BusinessRuleError(f"'{body.measure}' is a fixed attribute: no period or dates")

    if superseded is not None:
        new_key = (
            _enum(body.frequency),
            body.period_start,
            body.period_end,
            body.tax_year,
            as_of,
        )
        if _period_key(superseded) != new_key:
            raise BusinessRuleError(
                "A correction must cover the same period; to record a different period, "
                "record a new observation (and retire the old one if it was wrong)"
            )

    observation = FinancialObservation(
        tenant_id=ctx.tenant_id,
        case_id=case_id,
        income_source_id=body.income_source_id,
        commitment_id=body.commitment_id,
        expenditure_item_id=body.expenditure_item_id,
        measure=body.measure,
        value_type=definition.value_type,
        temporal_kind=definition.temporal_kind,
        frequency=_enum(body.frequency),
        period_start=body.period_start,
        period_end=body.period_end,
        tax_year=body.tax_year,
        as_of_date=as_of,
        source_kind=SourceKind(body.source_kind).value,
        recorded_by_user_id=ctx.actor.user_id,
        supersedes_observation_id=body.supersedes_observation_id,
        **_value_columns(body.value),
    )
    if superseded is not None:
        superseded.retired_at = utcnow()
        superseded.retired_reason = RetiredReason.SUPERSEDED.value
    ctx.session.add(observation)
    await ctx.session.flush()

    snapshot = audit.snapshot(
        observation,
        (
            "measure",
            "value_type",
            "source_kind",
            "frequency",
            *_VALUE_COLUMNS,
            "period_start",
            "period_end",
            "tax_year",
            "as_of_date",
        ),
    )
    change_set = audit.creation(snapshot).note(f"{subject_kind.value}_id", _subject_id(observation))
    change_set.note("supersedes_observation_id", body.supersedes_observation_id)
    await _record(
        ctx, AuditAction.OBSERVATION_RECORDED, "observation", observation.id, case_id, change_set
    )
    return _observation_out(observation)


async def retire_observation(
    ctx: OperationContext,
    case_id: uuid.UUID,
    observation_id: uuid.UUID,
    body: ObservationRetirement,
) -> ObservationOut:
    await _editable_case(ctx, case_id)
    observation: FinancialObservation | None = await ctx.session.scalar(
        select(FinancialObservation).where(
            FinancialObservation.id == observation_id, FinancialObservation.case_id == case_id
        )
    )
    if observation is None:
        raise NotFoundError("Observation not found")
    if observation.retired_at is not None:
        raise ConflictError("The observation has already been retired")
    observation.retired_at = utcnow()
    observation.retired_reason = RetiredReason(body.reason).value
    await ctx.session.flush()
    await _record(
        ctx,
        AuditAction.OBSERVATION_RETIRED,
        "observation",
        observation.id,
        case_id,
        audit.diff({"retired_reason": None}, {"retired_reason": observation.retired_reason}),
    )
    return _observation_out(observation)
