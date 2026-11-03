"""Map a validated extraction onto referral_fields rows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.db.constants import FIELD_KEYS
from app.db.models import ReferralField
from app.services.taxonomy import IDENTITY_FIELDS
from app.services.validation import FieldView

UNREADABLE_BELOW = 60.0


@dataclass(frozen=True)
class MappedField:
    key: str
    suggested_text: str | None
    suggested_json: Any
    ai_status: str
    evidence: list[dict[str, Any]]
    note: str | None


def _page(line_id: str) -> int:
    digits = ""
    for char in line_id[1:]:
        if char.isdigit():
            digits += char
        else:
            break
    return int(digits) if digits else 1


def _store_evidence(
    items: list[dict[str, Any]], elements: dict[str, dict[str, object]]
) -> list[dict[str, Any]]:
    stored: list[dict[str, Any]] = []
    for item in items:
        line_id = str(item.get("line_id", ""))
        element = elements.get(line_id, {})
        confidence = element.get("confidence")
        stored.append(
            {
                "line_id": line_id,
                "quote": str(item.get("quote", "")),
                "page": element.get("page") or _page(line_id),
                "bbox": element.get("bbox"),
                "confidence": confidence if isinstance(confidence, int | float) else None,
            }
        )
    return stored


def _minimum(evidence: list[dict[str, Any]]) -> float | None:
    scores = [
        float(item["confidence"])
        for item in evidence
        if isinstance(item.get("confidence"), int | float)
    ]
    return min(scores) if scores else None


def _blank_identity(key: str, evidence: list[dict[str, Any]]) -> bool:
    score = _minimum(evidence)
    return key in IDENTITY_FIELDS and score is not None and score < UNREADABLE_BELOW


def _from_model_field(
    key: str,
    field: dict[str, Any] | None,
    elements: dict[str, dict[str, object]],
    *,
    text: str | None = None,
    payload: Any = None,
) -> MappedField:
    if field is None:
        return MappedField(key, None, None, "not_found", [], None)
    evidence = _store_evidence(
        [item for item in field.get("evidence") or [] if isinstance(item, dict)],
        elements,
    )
    status = str(field.get("status") or "not_found")
    if status not in {"explicit", "normalized", "not_found", "ambiguous", "conflicting"}:
        status = "not_found"
    raw = text if text is not None else str(field.get("value") or "")
    note = str(field.get("note") or "") or None
    if _blank_identity(key, evidence):
        return MappedField(key, None, None, "not_found", evidence, note)
    if status in {"not_found", "conflicting"} or not raw.strip():
        shown = None
    else:
        shown = raw.strip()
    return MappedField(key, shown, payload, status, evidence, note)


def _join(parts: list[str]) -> str | None:
    cleaned = [part for part in parts if part.strip()]
    return "; ".join(cleaned) if cleaned else None


def view_from_row(row: ReferralField) -> FieldView:
    evidence = row.evidence if isinstance(row.evidence, list) else []
    return FieldView(
        key=row.field_key,
        ai_status=row.ai_status,
        suggested_text=row.suggested_text,
        confirmed_text=row.confirmed_text,
        confirmed_absent=row.confirmed_absent,
        evidence=evidence,
        note=row.note,
    )


def map_extraction(
    extraction: dict[str, Any],
    elements: dict[str, dict[str, object]],
) -> list[MappedField]:
    patient = extraction.get("patient") if isinstance(extraction.get("patient"), dict) else {}
    clinician = (
        extraction.get("referring_clinician")
        if isinstance(extraction.get("referring_clinician"), dict)
        else {}
    )
    referral = extraction.get("referral") if isinstance(extraction.get("referral"), dict) else {}
    mapped: dict[str, MappedField] = {}
    for key, owner, name in (
        ("patient.full_name", patient, "full_name"),
        ("patient.date_of_birth", patient, "date_of_birth"),
        ("patient.phone", patient, "phone"),
        ("patient.email", patient, "email"),
        ("patient.address", patient, "address"),
        ("referring_clinician.name", clinician, "name"),
        ("referring_clinician.organization", clinician, "organization"),
        ("referring_clinician.phone", clinician, "phone"),
        ("referring_clinician.fax", clinician, "fax"),
        ("referring_clinician.npi", clinician, "npi"),
        ("referral.referral_date", referral, "referral_date"),
        ("referral.reason", referral, "reason"),
    ):
        field = owner.get(name) if isinstance(owner, dict) else None
        mapped[key] = _from_model_field(key, field if isinstance(field, dict) else None, elements)

    identifiers = patient.get("identifiers") if isinstance(patient, dict) else []
    id_evidence: list[dict[str, Any]] = []
    id_parts: list[str] = []
    id_status = "not_found"
    if isinstance(identifiers, list) and identifiers:
        id_status = "explicit"
        for item in identifiers:
            if not isinstance(item, dict) or not isinstance(item.get("field"), dict):
                continue
            field = item["field"]
            label = str(item.get("label") or "").strip()
            value = str(field.get("value") or "").strip()
            if label or value:
                id_parts.append(" ".join(part for part in (label, value) if part))
            id_evidence.extend(
                entry for entry in field.get("evidence") or [] if isinstance(entry, dict)
            )
            if field.get("status") == "conflicting":
                id_status = "conflicting"
    stored_ids = _store_evidence(id_evidence, elements)
    if _blank_identity("patient.identifiers", stored_ids):
        mapped["patient.identifiers"] = MappedField(
            "patient.identifiers", None, None, "not_found", stored_ids, None
        )
    else:
        mapped["patient.identifiers"] = MappedField(
            "patient.identifiers",
            None if id_status in {"not_found", "conflicting"} else _join(id_parts),
            identifiers if isinstance(identifiers, list) else None,
            id_status,
            stored_ids,
            None,
        )

    priority = referral.get("sender_priority") if isinstance(referral, dict) else None
    if isinstance(priority, dict):
        mapped["referral.sender_priority"] = _from_model_field(
            "referral.sender_priority",
            priority,
            elements,
            text=str(priority.get("label") or ""),
            payload=priority,
        )
    requested = referral.get("requested_specialty") if isinstance(referral, dict) else None
    if isinstance(requested, dict):
        shown = str(requested.get("value") or requested.get("label") or "")
        mapped["referral.requested_specialty"] = _from_model_field(
            "referral.requested_specialty",
            requested,
            elements,
            text=shown,
            payload=requested,
        )
    codes = referral.get("diagnosis_codes") if isinstance(referral, dict) else []
    code_evidence: list[dict[str, Any]] = []
    code_parts: list[str] = []
    if isinstance(codes, list):
        for item in codes:
            if not isinstance(item, dict):
                continue
            code_parts.append(
                " ".join(
                    part
                    for part in (str(item.get("code") or ""), str(item.get("description") or ""))
                    if part
                ).strip()
            )
            code_evidence.extend(
                entry for entry in item.get("evidence") or [] if isinstance(entry, dict)
            )
    mapped["referral.diagnosis_codes"] = MappedField(
        "referral.diagnosis_codes",
        _join([part for part in code_parts if part]),
        codes if isinstance(codes, list) else None,
        "explicit" if code_parts else "not_found",
        _store_evidence(code_evidence, elements),
        None,
    )
    return [
        mapped.get(key) or MappedField(key, None, None, "not_found", [], None) for key in FIELD_KEYS
    ]
