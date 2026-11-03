"""RFC 9457 problem documents. Request values are not part of this model."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class FieldError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    loc: list[str | int]
    msg: str


class ProblemDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str
    title: str
    status: int
    detail: str
    code: str
    correlation_id: str
    errors: list[FieldError] | None = Field(default=None)
