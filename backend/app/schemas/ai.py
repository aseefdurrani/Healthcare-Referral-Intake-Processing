"""Pydantic mirror of referral_extraction_v1. Extra fields are rejected."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

FieldStatus = Literal["explicit", "normalized", "not_found", "ambiguous", "conflicting"]
SuggestionLabel = Literal[
    "cardiology",
    "dermatology",
    "gastroenterology",
    "neurology",
    "orthopedics",
    "pulmonology",
    "other_specialty",
    "uncertain",
]
CandidateLabel = Literal[
    "cardiology",
    "dermatology",
    "gastroenterology",
    "neurology",
    "orthopedics",
    "pulmonology",
    "other_specialty",
]
RequestedLabel = Literal[
    "cardiology",
    "dermatology",
    "gastroenterology",
    "neurology",
    "orthopedics",
    "pulmonology",
    "other_specialty",
    "none_stated",
]
PriorityLabel = Literal["urgent", "routine", "other", "not_marked"]
Relation = Literal[
    "matches_request", "differs_from_request", "no_request_stated", "request_unclear"
]
IssueType = Literal[
    "multiple_patients",
    "embedded_instructions",
    "conflicting_identifiers",
    "illegible_text",
    "possibly_incomplete",
    "not_a_referral",
    "other",
]


class EvidenceItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    line_id: str = Field(pattern=r"^p[1-9][0-9]*-(l|kv|cb)[0-9]{3}$")
    quote: str


class ExtractedField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str
    status: FieldStatus
    evidence: list[EvidenceItem]
    note: str


class IdentifierItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    field: ExtractedField


class PatientExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    full_name: ExtractedField
    date_of_birth: ExtractedField
    phone: ExtractedField
    email: ExtractedField
    address: ExtractedField
    identifiers: list[IdentifierItem]


class ClinicianExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: ExtractedField
    organization: ExtractedField
    phone: ExtractedField
    fax: ExtractedField
    npi: ExtractedField


class PriorityField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: PriorityLabel
    value: str
    status: FieldStatus
    evidence: list[EvidenceItem]
    note: str


class RequestedSpecialtyField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: RequestedLabel
    value: str
    status: FieldStatus
    evidence: list[EvidenceItem]
    note: str


class DiagnosisCode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    description: str
    evidence: list[EvidenceItem]


class ReferralExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    referral_date: ExtractedField
    reason: ExtractedField
    sender_priority: PriorityField
    requested_specialty: RequestedSpecialtyField
    diagnosis_codes: list[DiagnosisCode]


class SpecialtyCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: CandidateLabel
    reason: str


class SpecialtySuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: SuggestionLabel
    other_specialty_name: str
    candidates: list[SpecialtyCandidate]
    relation_to_request: Relation
    rationale: str
    evidence: list[EvidenceItem]


class DocumentFlags(BaseModel):
    model_config = ConfigDict(extra="forbid")

    multiple_patients_suspected: bool
    embedded_instructions_detected: bool
    illegible_or_incomplete_content: bool
    not_a_referral: bool


class ExtractionIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: IssueType
    detail: str
    evidence: list[EvidenceItem]


class ReferralExtractionV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_ref: str
    patient: PatientExtraction
    referring_clinician: ClinicianExtraction
    referral: ReferralExtraction
    specialty_suggestion: SpecialtySuggestion
    document_flags: DocumentFlags
    issues: list[ExtractionIssue]
