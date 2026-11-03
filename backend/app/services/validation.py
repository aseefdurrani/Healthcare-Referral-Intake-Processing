"""Deterministic checks. A conflicting field is resolved only by a staff value."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from app.services.grounding import _nanp, _npi

IDENTITY = frozenset({"patient.full_name", "patient.date_of_birth"})
PHONE_FIELDS = ("patient.phone", "referring_clinician.phone", "referring_clinician.fax")
CONTACT_FIELDS = ("patient.phone", "patient.email", "patient.address")


@dataclass(frozen=True)
class FieldView:
    key: str
    ai_status: str
    suggested_text: str | None
    confirmed_text: str | None
    confirmed_absent: bool
    evidence: list[dict[str, Any]]
    note: str | None


@dataclass(frozen=True)
class FlagDraft:
    code: str
    severity: str
    field_key: str | None
    message: str


def effective_text(field: FieldView) -> str | None:
    if field.confirmed_absent:
        return None
    if field.confirmed_text is not None and field.confirmed_text.strip():
        return field.confirmed_text.strip()
    if field.ai_status in {"not_found", "conflicting"}:
        return None
    if field.suggested_text is not None and field.suggested_text.strip():
        return field.suggested_text.strip()
    return None


def _missing(key: str, message: str) -> FlagDraft:
    return FlagDraft("MISSING_REQUIRED_FIELD", "blocking", key, message)


def _parse_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _age(born: date, today: date) -> int:
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))


def _confidence(field: FieldView) -> float | None:
    scores = [
        float(item["confidence"])
        for item in field.evidence
        if isinstance(item, dict) and isinstance(item.get("confidence"), int | float)
    ]
    if not scores:
        return None
    return min(scores)


def evaluate_rules(
    fields: list[FieldView],
    *,
    today: date | None = None,
    duplicate: bool = False,
    partial_ocr: bool = False,
    known_ids: set[str] | None = None,
    suggestion_label: str | None = None,
    check_specialty: bool = False,
) -> list[FlagDraft]:
    current = today or datetime.now().date()
    by_key = {field.key: field for field in fields}
    flags: list[FlagDraft] = []

    def text_of(key: str) -> str | None:
        field = by_key.get(key)
        return None if field is None else effective_text(field)

    def absent(key: str) -> bool:
        field = by_key.get(key)
        return field is not None and field.confirmed_absent

    name = text_of("patient.full_name")
    if not name:
        flags.append(_missing("patient.full_name", "Patient name was not found in the document."))
    dob = text_of("patient.date_of_birth")
    parsed_dob = _parse_date(dob) if dob else None
    if not dob:
        flags.append(
            _missing("patient.date_of_birth", "Date of birth was not found in the document.")
        )
    elif parsed_dob is None or parsed_dob > current or _age(parsed_dob, current) > 120:
        flags.append(
            FlagDraft(
                "INVALID_DATE_OF_BIRTH",
                "blocking",
                "patient.date_of_birth",
                "Date of birth is not a plausible past date.",
            )
        )
    if not text_of("referring_clinician.name") and not absent("referring_clinician.name"):
        flags.append(
            _missing("referring_clinician.name", "Referring clinician name was not found.")
        )
    if not text_of("referral.reason") and not absent("referral.reason"):
        flags.append(_missing("referral.reason", "Reason for referral was not found."))
    if not any(text_of(key) for key in CONTACT_FIELDS) and not all(
        absent(key) for key in CONTACT_FIELDS
    ):
        flags.append(_missing("patient.phone", "A patient phone, address, or email is required."))
    clinician_reach = ("referring_clinician.phone", "referring_clinician.fax")
    if not any(text_of(key) for key in clinician_reach) and not all(
        absent(key) for key in clinician_reach
    ):
        flags.append(_missing("referring_clinician.phone", "A clinician phone or fax is required."))
    for key in IDENTITY:
        field = by_key.get(key)
        if field is not None and field.ai_status == "conflicting" and not field.confirmed_text:
            flags.append(
                FlagDraft(
                    "PATIENT_IDENTIFIER_CONFLICT",
                    "blocking",
                    key,
                    "The document gives more than one value for this patient identifier.",
                )
            )
    for key in PHONE_FIELDS:
        value = text_of(key)
        if value and not _nanp(value):
            flags.append(
                FlagDraft(
                    "INVALID_PHONE_OR_FAX",
                    "warning",
                    key,
                    "This number is not a 10-digit NANP number.",
                )
            )
    npi = text_of("referring_clinician.npi")
    if npi and not _npi(re.sub(r"\D", "", npi) if not npi.isdigit() else npi):
        flags.append(
            FlagDraft(
                "INVALID_NPI",
                "warning",
                "referring_clinician.npi",
                "NPI is not 10 digits or does not pass the check digit.",
            )
        )
    referral_date = text_of("referral.referral_date")
    parsed_referral = _parse_date(referral_date) if referral_date else None
    if parsed_referral is not None and parsed_referral > current:
        flags.append(
            FlagDraft(
                "REFERRAL_DATE_IN_FUTURE",
                "warning",
                "referral.referral_date",
                "Referral date is later than today.",
            )
        )
    for field in fields:
        score = _confidence(field)
        if score is not None and score < 90:
            flags.append(
                FlagDraft(
                    "LOW_CONFIDENCE_FIELD",
                    "warning",
                    field.key,
                    "Verify this value against the document.",
                )
            )
        if field.ai_status == "ambiguous" and (field.note or "").strip():
            flags.append(
                FlagDraft(
                    "AI_NOTED_AMBIGUITY",
                    "warning",
                    field.key,
                    "The extraction noted more than one possible reading.",
                )
            )
        if known_ids is not None:
            for item in field.evidence:
                line_id = item.get("line_id") if isinstance(item, dict) else None
                if isinstance(line_id, str) and line_id not in known_ids:
                    flags.append(
                        FlagDraft(
                            "SOURCE_NOT_FOUND",
                            "warning",
                            field.key,
                            "A cited source is not in the OCR output.",
                        )
                    )
                    break
    if check_specialty and (suggestion_label is None or suggestion_label == ""):
        flags.append(
            FlagDraft(
                "NO_SPECIALTY_SUGGESTION",
                "warning",
                None,
                "No specialty suggestion was produced.",
            )
        )
    elif check_specialty and suggestion_label in {"uncertain", "other_specialty"}:
        flags.append(
            FlagDraft(
                "SPECIALTY_UNCERTAIN",
                "warning",
                None,
                "The specialty suggestion is uncertain.",
            )
        )
    if duplicate:
        flags.append(
            FlagDraft(
                "DUPLICATE_DOCUMENT",
                "warning",
                None,
                "This file matches an earlier referral.",
            )
        )
    if partial_ocr:
        flags.append(
            FlagDraft(
                "OCR_PARTIAL_RESULT",
                "warning",
                None,
                "Part of the document could not be read.",
            )
        )
    return flags
