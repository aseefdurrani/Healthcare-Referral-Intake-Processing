"""Referral API models. Field keys and the approval body follow the shared contract."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.services.taxonomy import ABSENT_ALLOWED

ReferralStatusName = Literal[
    "uploaded", "processing", "needs_review", "failed", "approved", "rejected"
]
FieldStatusName = Literal["explicit", "normalized", "not_found", "ambiguous", "conflicting"]
ReadabilityName = Literal["verify", "unreadable"]
ActionName = Literal["edit", "approve", "reject", "retry"]
RunStateName = Literal["queued", "running", "succeeded", "failed"]
PriorityName = Literal["urgent", "routine", "other", "not_marked"]
ReasonCode = Literal["not_a_referral", "unreadable", "duplicate", "incomplete", "other"]
ProcessingStep = Literal["reading_text", "structuring", "validating"]


class BoundingBox(BaseModel):
    model_config = ConfigDict(extra="forbid")

    left: float
    top: float
    width: float
    height: float


class SourceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page: int = Field(ge=1)
    element_ids: list[str]
    bbox: BoundingBox


class ReferralFieldOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    label: str
    ocr_value: str | None
    suggested_value: str | None
    confirmed_value: str | None
    effective_value: str | None
    field_status: FieldStatusName
    readability: ReadabilityName | None
    source: SourceRef | None
    edited: bool


class ValidationFlagOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    code: str
    severity: Literal["blocking", "warning"]
    field_key: str | None
    message: str
    status: Literal["open", "resolved", "acknowledged"]
    acknowledgement_reason: str | None


class SpecialtyCandidateOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    reason: str


class SpecialtySuggestionOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    display_name: str
    rationale: str | None
    candidates: list[SpecialtyCandidateOut] = Field(default_factory=list, max_length=2)


class DestinationSpecialty(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    other_name: str | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def other_needs_a_name(self) -> DestinationSpecialty:
        if self.code == "other":
            if self.other_name is None or not self.other_name.strip():
                raise ValueError("Field required")
        elif self.other_name:
            raise ValueError("Invalid value")
        return self


class ProcessingRunOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    attempt: int
    state: RunStateName
    error_code: str | None
    retryable: bool | None


class DocumentOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    display_filename: str
    content_type: str
    size_bytes: int | None
    page_count: int | None


class ReferralSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    status: ReferralStatusName
    version: int
    created_at: datetime
    updated_at: datetime
    document: DocumentOut
    patient_name: str | None
    suggested_specialty: str | None
    blocking_flag_count: int
    warning_flag_count: int
    possible_duplicate_of: uuid.UUID | None


class ReferralEventOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str
    actor: Literal["staff", "system"]
    from_status: ReferralStatusName | None
    to_status: ReferralStatusName | None
    at: datetime
    reason: str | None


class ReferralDetail(ReferralSummary):
    allowed_actions: list[ActionName]
    fields: list[ReferralFieldOut]
    flags: list[ValidationFlagOut]
    specialty: SpecialtySuggestionOut | None
    specialty_decision: Literal["accept_suggested", "override"] | None
    destination_specialty: DestinationSpecialty | None
    current_run: ProcessingRunOut | None
    history: list[ReferralEventOut]


class ReferralPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ReferralSummary]
    total: int
    limit: int
    offset: int


class FailureSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step: ProcessingStep | None
    message: str
    retryable: bool
    run_id: uuid.UUID


class ReferralStatusOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    status: ReferralStatusName
    version: int
    processing_step: ProcessingStep | None
    failure: FailureSummary | None


class FieldCorrection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    value: str | None = Field(default=None, max_length=500)

    @field_validator("value")
    @classmethod
    def strip_value(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip()


class ReferralPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    fields: list[FieldCorrection] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def identity_cannot_be_absent(self) -> ReferralPatch:
        for item in self.fields:
            if item.value is None and item.key in {"patient.full_name", "patient.date_of_birth"}:
                raise ValueError("Invalid value")
        return self


class Acknowledgement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    flag_id: uuid.UUID
    reason: str = Field(min_length=3, max_length=500)


class ApproveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    destination_specialty: DestinationSpecialty
    sender_priority: PriorityName
    not_on_document: list[str] = Field(default_factory=list)
    acknowledgements: list[Acknowledgement] = Field(default_factory=list)
    note: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def absent_keys_are_allowed(self) -> ApproveRequest:
        for key in self.not_on_document:
            if key not in ABSENT_ALLOWED:
                raise ValueError("Invalid value")
        return self


class RejectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    reason_code: ReasonCode
    note: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def other_needs_a_note(self) -> RejectRequest:
        if self.reason_code == "other" and (self.note is None or not self.note.strip()):
            raise ValueError("Field required")
        return self


class RetryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
