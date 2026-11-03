"""Closed lists stored as text checks. Phase 3 reads these; the migration freezes its own copy."""

FIELD_KEYS: tuple[str, ...] = (
    "patient.full_name",
    "patient.date_of_birth",
    "patient.phone",
    "patient.email",
    "patient.address",
    "patient.identifiers",
    "referring_clinician.name",
    "referring_clinician.organization",
    "referring_clinician.phone",
    "referring_clinician.fax",
    "referring_clinician.npi",
    "referral.referral_date",
    "referral.reason",
    "referral.sender_priority",
    "referral.requested_specialty",
    "referral.diagnosis_codes",
)

REFERRAL_STATUSES: tuple[str, ...] = (
    "uploaded",
    "processing",
    "needs_review",
    "failed",
    "approved",
    "rejected",
)

DESTINATION_SPECIALTIES: tuple[str, ...] = (
    "cardiology",
    "dermatology",
    "gastroenterology",
    "neurology",
    "orthopedics",
    "pulmonology",
    "other",
)

SUGGESTED_SPECIALTIES: tuple[str, ...] = (
    "cardiology",
    "dermatology",
    "gastroenterology",
    "neurology",
    "orthopedics",
    "pulmonology",
    "other_specialty",
    "uncertain",
)

AUDIT_ACTIONS: tuple[str, ...] = (
    "uploaded",
    "referral_viewed",
    "document_viewed",
    "field_corrected",
    "specialty_changed",
    "approved",
    "rejected",
    "reprocess_requested",
    "processing_started",
    "processing_failed",
    "ready_for_review",
)

TABLES: tuple[str, ...] = (
    "referrals",
    "documents",
    "processing_runs",
    "ocr_runs",
    "ai_runs",
    "referral_fields",
    "validation_flags",
    "audit_events",
)
