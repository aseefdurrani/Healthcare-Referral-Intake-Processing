"""Display labels for the field keys and specialty codes stored by the schema."""

from __future__ import annotations

from app.db.constants import DESTINATION_SPECIALTIES, FIELD_KEYS, SUGGESTED_SPECIALTIES

FIELD_LABELS: dict[str, str] = {
    "patient.full_name": "Patient name",
    "patient.date_of_birth": "Date of birth",
    "patient.phone": "Patient phone",
    "patient.email": "Patient email",
    "patient.address": "Patient address",
    "patient.identifiers": "Patient identifiers",
    "referring_clinician.name": "Referring clinician",
    "referring_clinician.organization": "Referring organization",
    "referring_clinician.phone": "Referring clinician phone",
    "referring_clinician.fax": "Referring clinician fax",
    "referring_clinician.npi": "Referring clinician NPI",
    "referral.referral_date": "Referral date",
    "referral.reason": "Reason for referral",
    "referral.sender_priority": "Sender priority",
    "referral.requested_specialty": "Requested specialty",
    "referral.diagnosis_codes": "Diagnosis codes",
}

SPECIALTY_LABELS: dict[str, str] = {
    "cardiology": "Cardiology",
    "dermatology": "Dermatology",
    "gastroenterology": "Gastroenterology",
    "neurology": "Neurology",
    "orthopedics": "Orthopedics",
    "pulmonology": "Pulmonology",
    "other_specialty": "Other specialty",
    "uncertain": "Uncertain",
    "other": "Other",
    "none_stated": "None stated",
}

IDENTITY_FIELDS = frozenset(
    {
        "patient.full_name",
        "patient.date_of_birth",
        "patient.phone",
        "patient.identifiers",
    }
)

ABSENT_ALLOWED = frozenset(
    {
        "patient.phone",
        "patient.email",
        "patient.address",
        "referring_clinician.name",
        "referring_clinician.phone",
        "referring_clinician.fax",
        "referral.reason",
    }
)

ACK_FLAG_CODES = frozenset(
    {
        "specialty_mismatch",
        "request_unclear",
        "multiple_patients_suspected",
        "embedded_instructions_detected",
        "illegible_or_incomplete_content",
        "not_a_referral",
    }
)

KNOWN_FIELDS = frozenset(FIELD_KEYS)
DESTINATIONS = frozenset(DESTINATION_SPECIALTIES)
SUGGESTIONS = frozenset(SUGGESTED_SPECIALTIES)


def field_label(key: str) -> str:
    return FIELD_LABELS.get(key, key)


def specialty_label(code: str) -> str:
    return SPECIALTY_LABELS.get(code, code)
