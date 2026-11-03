"""Soft checks on a validated extraction. The model output is not changed."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

_DATE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")
_ICD = re.compile(r"^[A-Z][0-9][0-9A-Z](\.[0-9A-Z]{1,4})?$")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

_FLAG_ISSUE = {
    "multiple_patients_suspected": "multiple_patients",
    "embedded_instructions_detected": "embedded_instructions",
    "not_a_referral": "not_a_referral",
}
_ISSUE_FLAG = {value: key for key, value in _FLAG_ISSUE.items()}
_ISSUE_FLAG["illegible_text"] = "illegible_or_incomplete_content"
_ISSUE_FLAG["possibly_incomplete"] = "illegible_or_incomplete_content"

PHONE_FIELDS = {"patient.phone", "referring_clinician.phone", "referring_clinician.fax"}


def _norm(value: str) -> str:
    return " ".join(value.split()).casefold()


def _digits(value: str) -> str:
    return "".join(ch for ch in value if ch.isdigit())


def _warnings_for_evidence(
    path: str,
    evidence: list[dict[str, Any]],
    line_map: dict[str, str],
) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    for item in evidence:
        line_id = str(item.get("line_id", ""))
        source = line_map.get(line_id)
        if source is None:
            found.append({"path": path, "code": "evidence_unknown_line"})
            continue
        quote = str(item.get("quote", ""))
        if len(quote) > 200:
            found.append({"path": path, "code": "too_long"})
        if _norm(quote) not in _norm(source):
            found.append({"path": path, "code": "evidence_unverified"})
    return found


def _value_grounded(
    path: str, value: str, status: str, evidence: list[dict[str, Any]]
) -> str | None:
    if status not in {"explicit", "normalized"} or not value:
        return None
    quotes = " ".join(str(item.get("quote", "")) for item in evidence)
    if status == "explicit":
        if path in PHONE_FIELDS or path.endswith(".fax"):
            digits = _digits(value)
            if digits and digits not in _digits(quotes):
                return "value_unverified"
            return None
        if _norm(value) not in _norm(quotes):
            return "value_unverified"
        return None
    if path.endswith("date_of_birth") or path.endswith("referral_date"):
        match = _DATE.search(quotes)
        if match is None:
            return "value_unverified"
        month, day, year = (int(part) for part in match.groups())
        try:
            parsed = date(year, month, day).isoformat()
        except ValueError:
            return "value_unverified"
        if parsed != value:
            return "value_unverified"
    return None


def _format_code(path: str, value: str, today: date) -> str | None:
    if not value:
        return None
    if path.endswith("date_of_birth") or path.endswith("referral_date"):
        try:
            parsed = date.fromisoformat(value)
        except ValueError:
            return "format_invalid"
        if parsed > today:
            return "format_invalid"
        if path.endswith("date_of_birth"):
            years = (
                today.year - parsed.year - ((today.month, today.day) < (parsed.month, parsed.day))
            )
            if years > 120:
                return "format_invalid"
        return None
    if path in PHONE_FIELDS and not _nanp(value):
        return "format_invalid"
    if path.endswith("email") and _EMAIL.fullmatch(value) is None:
        return "format_invalid"
    if path.endswith("npi") and not _npi(value):
        return "format_invalid"
    return None


def _nanp(value: str) -> bool:
    digits = _digits(value)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return len(digits) == 10 and digits[0] not in "01" and digits[3] not in "01"


def _npi(value: str) -> bool:
    if re.fullmatch(r"\d{10}", value) is None:
        return False
    digits = [int(ch) for ch in "80840" + value]
    total = 0
    for index, digit in enumerate(reversed(digits)):
        if index % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def _field_warnings(
    path: str,
    field: dict[str, Any],
    line_map: dict[str, str],
    today: date,
) -> list[dict[str, str]]:
    status = str(field.get("status", ""))
    value = str(field.get("value", ""))
    evidence = [item for item in field.get("evidence") or [] if isinstance(item, dict)]
    note = str(field.get("note", ""))
    found: list[dict[str, str]] = []
    if status in {"not_found", "conflicting"} and value != "":
        found.append({"path": path, "code": "status_value_mismatch"})
    elif value and not evidence:
        found.append({"path": path, "code": "status_value_mismatch"})
    else:
        found.extend(_warnings_for_evidence(path, evidence, line_map))
        if not any(item["code"] == "evidence_unverified" for item in found):
            code = _value_grounded(path, value, status, evidence)
            if code is not None:
                found.append({"path": path, "code": code})
    format_code = _format_code(path, value, today)
    if format_code is not None:
        found.append({"path": path, "code": format_code})
    if len(note) > 300:
        found.append({"path": path, "code": "too_long"})
    return found


def ground(
    extraction: dict[str, Any],
    lines: dict[str, str],
    *,
    today: date | None = None,
) -> list[dict[str, str]]:
    """Return warning codes only. Each item is ``{"path", "code"}``."""

    current = today or datetime.now().date()
    found: list[dict[str, str]] = []
    patient = extraction.get("patient") or {}
    for name in ("full_name", "date_of_birth", "phone", "email", "address"):
        field = patient.get(name)
        if isinstance(field, dict):
            found.extend(_field_warnings(f"patient.{name}", field, lines, current))
    for index, item in enumerate(patient.get("identifiers") or []):
        if isinstance(item, dict) and isinstance(item.get("field"), dict):
            found.extend(
                _field_warnings(f"patient.identifiers[{index}]", item["field"], lines, current)
            )
    clinician = extraction.get("referring_clinician") or {}
    for name in ("name", "organization", "phone", "fax", "npi"):
        field = clinician.get(name)
        if isinstance(field, dict):
            found.extend(_field_warnings(f"referring_clinician.{name}", field, lines, current))
    referral = extraction.get("referral") or {}
    for name in ("referral_date", "reason"):
        field = referral.get(name)
        if isinstance(field, dict):
            found.extend(_field_warnings(f"referral.{name}", field, lines, current))
    priority = referral.get("sender_priority")
    if isinstance(priority, dict):
        found.extend(_field_warnings("referral.sender_priority", priority, lines, current))
    requested = referral.get("requested_specialty")
    if isinstance(requested, dict):
        found.extend(_field_warnings("referral.requested_specialty", requested, lines, current))
    for index, code in enumerate(referral.get("diagnosis_codes") or []):
        if isinstance(code, dict):
            evidence = [item for item in code.get("evidence") or [] if isinstance(item, dict)]
            found.extend(
                _warnings_for_evidence(f"referral.diagnosis_codes[{index}]", evidence, lines)
            )
            printed = str(code.get("code", ""))
            if printed and _ICD.fullmatch(printed) is None:
                found.append(
                    {"path": f"referral.diagnosis_codes[{index}]", "code": "format_invalid"}
                )
    suggestion = extraction.get("specialty_suggestion") or {}
    if isinstance(suggestion, dict):
        found.extend(
            _specialty_warnings(suggestion, requested if isinstance(requested, dict) else {}, lines)
        )
    found.extend(_flag_warnings(extraction))
    return _unique(found)


def _specialty_warnings(
    suggestion: dict[str, Any],
    requested: dict[str, Any],
    lines: dict[str, str],
) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    evidence = [item for item in suggestion.get("evidence") or [] if isinstance(item, dict)]
    evidence_warnings = _warnings_for_evidence("specialty_suggestion", evidence, lines)
    found.extend(evidence_warnings)
    verified = not evidence_warnings and len(evidence) > 0
    label = str(suggestion.get("label", ""))
    candidates = suggestion.get("candidates") or []
    if label == "uncertain" and not 1 <= len(candidates) <= 2:
        found.append({"path": "specialty_suggestion", "code": "candidates_invalid"})
    elif label == "other_specialty" and not str(suggestion.get("other_specialty_name", "")).strip():
        found.append({"path": "specialty_suggestion", "code": "candidates_invalid"})
    elif label not in {"uncertain", "other_specialty"}:
        if candidates:
            found.append({"path": "specialty_suggestion", "code": "candidates_invalid"})
        if not verified:
            found.append({"path": "specialty_suggestion", "code": "suggestion_unsupported"})
    rationale = str(suggestion.get("rationale", ""))
    if len(rationale) > 300:
        found.append({"path": "specialty_suggestion", "code": "too_long"})
    requested_label = str(requested.get("label", ""))
    if (
        requested_label
        and requested_label != "none_stated"
        and label
        and label != "uncertain"
        and requested_label != label
    ):
        found.append({"path": "specialty_suggestion", "code": "specialty_mismatch"})
    return found


def _flag_warnings(extraction: dict[str, Any]) -> list[dict[str, str]]:
    flags = extraction.get("document_flags") or {}
    issues = extraction.get("issues") or []
    issue_types = {str(issue.get("type")) for issue in issues if isinstance(issue, dict)}
    found: list[dict[str, str]] = []
    if not isinstance(flags, dict):
        return found
    for flag_name, issue_type in _FLAG_ISSUE.items():
        if flags.get(flag_name) and issue_type not in issue_types:
            found.append({"path": "document_flags", "code": "flag_issue_inconsistent"})
    illegible = bool(flags.get("illegible_or_incomplete_content"))
    if illegible and not ({"illegible_text", "possibly_incomplete"} & issue_types):
        found.append({"path": "document_flags", "code": "flag_issue_inconsistent"})
    for issue_type in issue_types:
        mapped = _ISSUE_FLAG.get(issue_type)
        if mapped is not None and not flags.get(mapped):
            found.append({"path": "issues", "code": "flag_issue_inconsistent"})
    return found


def _unique(items: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[tuple[str, str]] = set()
    unique: list[dict[str, str]] = []
    for item in items:
        key = (item["path"], item["code"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique
