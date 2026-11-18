from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal, Self

from pydantic import AfterValidator, Field, model_validator

from meip.core.schemas import InputModel, OutputModel, ShortText
from meip.financial.measures import (
    CommitmentKind,
    CommitmentStatus,
    EmploymentBasis,
    ExpenditureCategory,
    ExpenditureScope,
    Frequency,
    IncomeKind,
    LiabilityRole,
    RetiredReason,
    ShareBasis,
    SourceKind,
    SubjectKind,
    TemporalKind,
    ValueType,
    WithdrawalReason,
)

_FOUR_PLACES = Decimal("0.0001")


def _four_places(value: Decimal) -> Decimal:
    """Normalise to the stored scale so create and read responses render identically."""
    return value.quantize(_FOUR_PLACES)


Percent0To100 = Annotated[
    Decimal, Field(ge=0, le=100, max_digits=7, decimal_places=4), AfterValidator(_four_places)
]
AccountRef = Annotated[str, Field(pattern=r"^[A-Za-z0-9]{1,4}$", description="Last 4 chars only")]
TaxYear = Annotated[str, Field(pattern=r"^\d{4}-\d{2}$", examples=["2025-26"])]


# --- typed values ------------------------------------------------------------------------


class MoneyValue(OutputModel):
    type: Literal["money", "signed_money"]
    amount_minor: int = Field(description="Minor units (pence); negative only for signed_money")
    currency: Literal["GBP"] = "GBP"

    @model_validator(mode="after")
    def _sign(self) -> Self:
        if self.type == "money" and self.amount_minor < 0:
            raise ValueError("money values cannot be negative; use a signed_money measure")
        return self


class PercentValue(OutputModel):
    type: Literal["rate", "proportion"]
    percent: Annotated[
        Decimal, Field(ge=0, max_digits=10, decimal_places=4), AfterValidator(_four_places)
    ]

    @model_validator(mode="after")
    def _range(self) -> Self:
        if self.type == "proportion" and self.percent > 100:
            raise ValueError("a proportion cannot exceed 100")
        return self


class DurationValue(OutputModel):
    type: Literal["duration"]
    months: int = Field(ge=0)


class CountValue(OutputModel):
    type: Literal["count"]
    count: int = Field(ge=0)


class DateValue(OutputModel):
    type: Literal["date"]
    date: date


ObservationValue = Annotated[
    MoneyValue | PercentValue | DurationValue | CountValue | DateValue,
    Field(discriminator="type"),
]


# --- measures ----------------------------------------------------------------------------


class MeasureOut(OutputModel):
    """A measure's meaning never changes; deprecation only stops new values."""

    code: str
    subject_kind: SubjectKind
    value_type: ValueType
    temporal_kind: TemporalKind
    description: str
    qualifier_required: bool
    qualifiers: list[str] = Field(
        description="Allowed qualifiers. A single label, not an exhaustive classification."
    )
    deprecated_at: datetime | None


# --- income sources ----------------------------------------------------------------------


class IncomeSourceOut(OutputModel):
    id: uuid.UUID
    case_id: uuid.UUID
    applicant_id: uuid.UUID
    kind: IncomeKind
    name: str | None
    basis: EmploymentBasis | None
    is_primary: bool
    start_date: date | None
    end_date: date | None
    ownership_share: Decimal | None
    withdrawn_at: datetime | None = Field(
        default=None, description="Set if the item was withdrawn (entered in error)"
    )
    withdrawal_reason: WithdrawalReason | None = None
    created_at: datetime
    updated_at: datetime
    version: int


class IncomeSourceCreate(InputModel):
    applicant_id: uuid.UUID
    kind: IncomeKind
    name: ShortText | None = Field(default=None, description="Employer or business name")
    basis: EmploymentBasis | None = None
    is_primary: bool = Field(default=False, description="The applicant's main employment")
    start_date: date | None = None
    end_date: date | None = None
    ownership_share: Percent0To100 | None = Field(
        default=None, description="For jointly held sources (e.g. a let property)"
    )


class IncomeSourceUpdate(InputModel):
    """Partial update; send the item's ETag in ``If-Match``."""

    kind: IncomeKind | None = None
    name: ShortText | None = None
    basis: EmploymentBasis | None = None
    is_primary: bool | None = None
    start_date: date | None = None
    end_date: date | None = None
    ownership_share: Percent0To100 | None = None


# --- commitments -------------------------------------------------------------------------


class CommitmentParty(OutputModel):
    applicant_id: uuid.UUID
    liability_role: LiabilityRole = LiabilityRole.PRINCIPAL
    responsibility_share: Percent0To100 | None = Field(
        default=None, description="Who actually pays (an allocation, not liability)"
    )
    share_basis: ShareBasis | None = None

    @model_validator(mode="after")
    def _share(self) -> Self:
        if (self.responsibility_share is None) != (self.share_basis is None):
            raise ValueError("responsibility_share and share_basis are set together")
        return self


class CommitmentOut(OutputModel):
    id: uuid.UUID
    case_id: uuid.UUID
    kind: CommitmentKind
    creditor: str | None
    account_ref_masked: str | None
    status: CommitmentStatus
    to_be_repaid_before_completion: bool | None
    parties: list[CommitmentParty] = Field(description="Who is legally liable")
    withdrawn_at: datetime | None = Field(
        default=None, description="Set if the item was withdrawn (entered in error)"
    )
    withdrawal_reason: WithdrawalReason | None = None
    created_at: datetime
    updated_at: datetime
    version: int


def _unique_applicants(parties: list[CommitmentParty] | list[ExpenditureParty]) -> None:
    ids = [p.applicant_id for p in parties]
    if len(ids) != len(set(ids)):
        raise ValueError("each applicant may appear only once")


class CommitmentCreate(InputModel):
    kind: CommitmentKind
    creditor: ShortText | None = None
    account_ref_masked: AccountRef | None = None
    status: CommitmentStatus = CommitmentStatus.OPEN
    to_be_repaid_before_completion: bool | None = None
    parties: list[CommitmentParty] = Field(min_length=1)

    @model_validator(mode="after")
    def _parties(self) -> Self:
        _unique_applicants(self.parties)
        return self


class CommitmentUpdate(InputModel):
    """Partial update; send the item's ETag in ``If-Match``. ``parties`` replaces the list."""

    kind: CommitmentKind | None = None
    creditor: ShortText | None = None
    account_ref_masked: AccountRef | None = None
    status: CommitmentStatus | None = None
    to_be_repaid_before_completion: bool | None = None
    parties: list[CommitmentParty] | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def _parties(self) -> Self:
        if self.parties is not None:
            _unique_applicants(self.parties)
        return self


# --- expenditure -------------------------------------------------------------------------


class ExpenditureParty(OutputModel):
    applicant_id: uuid.UUID
    share: Percent0To100 | None = None


class ExpenditureOut(OutputModel):
    id: uuid.UUID
    case_id: uuid.UUID
    category: ExpenditureCategory
    scope: ExpenditureScope
    description: str | None
    parties: list[ExpenditureParty]
    withdrawn_at: datetime | None = Field(
        default=None, description="Set if the item was withdrawn (entered in error)"
    )
    withdrawal_reason: WithdrawalReason | None = None
    created_at: datetime
    updated_at: datetime
    version: int


def _check_scope(scope: ExpenditureScope, parties: list[ExpenditureParty]) -> None:
    if scope is ExpenditureScope.HOUSEHOLD and parties:
        raise ValueError("household expenditure has no parties")
    if scope is ExpenditureScope.INDIVIDUAL and not parties:
        raise ValueError("individual expenditure needs at least one party")
    _unique_applicants(parties)


class ExpenditureCreate(InputModel):
    category: ExpenditureCategory
    scope: ExpenditureScope
    description: ShortText | None = None
    parties: list[ExpenditureParty] = Field(default_factory=list)

    @model_validator(mode="after")
    def _parties(self) -> Self:
        _check_scope(self.scope, self.parties)
        return self


class ExpenditureUpdate(InputModel):
    """Partial update; send the item's ETag in ``If-Match``. Scope and parties change
    together: send both, and ``parties`` replaces the list."""

    category: ExpenditureCategory | None = None
    description: ShortText | None = None
    scope: ExpenditureScope | None = None
    parties: list[ExpenditureParty] | None = None

    @model_validator(mode="after")
    def _parties(self) -> Self:
        if (self.scope is None) != (self.parties is None):
            raise ValueError("send scope and parties together")
        if self.scope is not None and self.parties is not None:
            _check_scope(self.scope, self.parties)
        return self


# --- observations ------------------------------------------------------------------------


class ObservationOut(OutputModel):
    id: uuid.UUID
    case_id: uuid.UUID
    subject_kind: SubjectKind
    income_source_id: uuid.UUID | None
    commitment_id: uuid.UUID | None
    expenditure_item_id: uuid.UUID | None
    measure: str
    qualifier: str | None
    value: ObservationValue
    frequency: Frequency | None
    period_start: date | None
    period_end: date | None
    tax_year: str | None
    as_of_date: date | None
    source_kind: SourceKind
    recorded_by_user_id: uuid.UUID | None
    recorded_at: datetime
    supersedes_observation_id: uuid.UUID | None
    superseded_by_observation_id: uuid.UUID | None = Field(
        default=None, description="The correction that replaced this value, if any"
    )
    retired_at: datetime | None
    retired_reason: RetiredReason | None


class ObservationCreate(InputModel):
    """Record one typed value about one item. To correct a value, record a new observation
    with ``supersedes_observation_id``; the old one is retired, never edited."""

    income_source_id: uuid.UUID | None = None
    commitment_id: uuid.UUID | None = None
    expenditure_item_id: uuid.UUID | None = None
    measure: str = Field(min_length=1, max_length=48, examples=["basic_pay"])
    qualifier: str | None = Field(
        default=None,
        max_length=32,
        description="Required for allowance and bonus (see GET /api/v1/financial/measures)",
    )
    value: ObservationValue
    frequency: Frequency | None = Field(default=None, description="Required for flow measures")
    period_start: date | None = None
    period_end: date | None = None
    tax_year: TaxYear | None = None
    as_of_date: date | None = Field(
        default=None, description="Stock measures: defaults to today if omitted"
    )
    source_kind: Literal[SourceKind.DECLARED, SourceKind.BROKER_ENTRY] = SourceKind.DECLARED
    supersedes_observation_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _one_subject(self) -> Self:
        subjects = [self.income_source_id, self.commitment_id, self.expenditure_item_id]
        if sum(s is not None for s in subjects) != 1:
            raise ValueError(
                "set exactly one of income_source_id, commitment_id, expenditure_item_id"
            )
        if self.period_start and self.period_end and self.period_end < self.period_start:
            raise ValueError("period_end is before period_start")
        return self

    @property
    def subject_kind(self) -> SubjectKind:
        if self.income_source_id is not None:
            return SubjectKind.INCOME_SOURCE
        if self.commitment_id is not None:
            return SubjectKind.COMMITMENT
        return SubjectKind.EXPENDITURE


class WithdrawalRequest(InputModel):
    """Withdraw an item that should never have existed. Irreversible: to correct a
    withdrawal, create a new item. Its active values are retired with it."""

    reason: WithdrawalReason


class ObservationRetirement(InputModel):
    reason: Literal[RetiredReason.ENTERED_IN_ERROR] = Field(
        description="Corrections use supersedes_observation_id on a new observation instead"
    )
