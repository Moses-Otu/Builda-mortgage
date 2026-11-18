"""Money representation.

All monetary amounts are integer minor units (pence). Floats are never used for money.
The MVP is GBP-only; ``currency`` is explicit so the type can widen later without a
breaking change to the API contract.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Currency = Literal["GBP"]


class Money(BaseModel):
    model_config = ConfigDict(
        frozen=True, json_schema_extra={"example": {"amount_minor": 4500000, "currency": "GBP"}}
    )

    amount_minor: int = Field(
        ge=0, description="Amount in minor units (pence). 4500000 = £45,000.00"
    )
    currency: Currency = "GBP"

    @property
    def major(self) -> Decimal:
        return Decimal(self.amount_minor) / Decimal(100)

    @classmethod
    def gbp(cls, amount_minor: int) -> Money:
        return cls(amount_minor=amount_minor, currency="GBP")

    @classmethod
    def from_optional_minor(cls, amount_minor: int | None) -> Money | None:
        return None if amount_minor is None else cls.gbp(amount_minor)
