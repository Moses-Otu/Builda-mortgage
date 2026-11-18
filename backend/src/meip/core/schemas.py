"""Shared API schema building blocks."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


class InputModel(BaseModel):
    """Base for request bodies: unknown fields are rejected, not silently ignored."""

    model_config = ConfigDict(extra="forbid")


class OutputModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page[T](BaseModel):
    items: list[T]
    total: int
    limit: int
    offset: int


Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
Version = Annotated[int, Field(ge=1, description="Current version, for optimistic concurrency")]
