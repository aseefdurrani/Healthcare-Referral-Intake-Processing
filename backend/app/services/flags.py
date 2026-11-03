"""The full flag set for a referral, recomputed after extraction and after each edit."""

from __future__ import annotations

from datetime import date
from typing import Any

from app.services.grounding import ground
from app.services.taxonomy import KNOWN_FIELDS
from app.services.validation import FieldView, FlagDraft, evaluate_rules

_DOCUMENT_FLAGS = {
    "multiple_patients_suspected": "The document may name more than one patient.",
    "embedded_instructions_detected": "The document addresses an automated reader.",
    "illegible_or_incomplete_content": "Part of the document is illegible or incomplete.",
    "not_a_referral": "The document may not be a referral.",
}


def _field_key(path: str) -> str | None:
    if path in KNOWN_FIELDS:
        return path
    if path.startswith("patient.identifiers"):
        return "patient.identifiers"
    if path.startswith("referral.diagnosis_codes"):
        return "referral.diagnosis_codes"
    return None


def _grounding_flags(warnings: list[dict[str, str]]) -> list[FlagDraft]:
    drafts: list[FlagDraft] = []
    for warning in warnings:
        code = warning["code"]
        key = _field_key(warning["path"])
        if code == "evidence_unknown_line":
            drafts.append(
                FlagDraft(
                    "SOURCE_NOT_FOUND", "warning", key, "A cited source is not in the OCR output."
                )
            )
        elif code == "specialty_mismatch":
            drafts.append(
                FlagDraft(
                    "specialty_mismatch",
                    "blocking",
                    None,
                    "The requested specialty and the suggestion differ.",
                )
            )
        elif code == "flag_issue_inconsistent":
            drafts.append(
                FlagDraft(code, "warning", None, "A document flag and its issue do not agree.")
            )
        else:
            drafts.append(
                FlagDraft(code, "warning", key, "Review this suggestion against the document.")
            )
    return drafts


def _document_flags(extraction: dict[str, Any]) -> list[FlagDraft]:
    raw_flags = extraction.get("document_flags")
    flags = raw_flags if isinstance(raw_flags, dict) else {}
    drafts = [
        FlagDraft(code, "blocking", None, message)
        for code, message in _DOCUMENT_FLAGS.items()
        if flags.get(code)
    ]
    raw_suggestion = extraction.get("specialty_suggestion")
    suggestion = raw_suggestion if isinstance(raw_suggestion, dict) else {}
    requested: dict[str, Any] = {}
    referral = extraction.get("referral")
    if isinstance(referral, dict) and isinstance(referral.get("requested_specialty"), dict):
        requested = referral["requested_specialty"]
    requested_label = str(requested.get("label") or "")
    suggestion_label = str(suggestion.get("label") or "")
    if (
        requested_label
        and requested_label != "none_stated"
        and suggestion_label
        and suggestion_label != "uncertain"
        and requested_label != suggestion_label
    ):
        drafts.append(
            FlagDraft(
                "specialty_mismatch",
                "blocking",
                None,
                "The requested specialty and the suggestion differ.",
            )
        )
    if suggestion.get("relation_to_request") == "request_unclear":
        drafts.append(
            FlagDraft("request_unclear", "blocking", None, "The requested service is unclear.")
        )
    return drafts


def assemble_flags(
    fields: list[FieldView],
    *,
    today: date | None = None,
    duplicate: bool = False,
    ocr_flag_codes: list[str] | None = None,
    known_ids: set[str] | None = None,
    extraction: dict[str, Any] | None = None,
    lines: dict[str, str] | None = None,
    run_error_code: str | None = None,
) -> list[FlagDraft]:
    suggestion = None
    check_specialty = extraction is not None
    if isinstance(extraction, dict):
        raw = extraction.get("specialty_suggestion")
        if isinstance(raw, dict):
            suggestion = str(raw.get("label") or "")
    drafts = evaluate_rules(
        fields,
        today=today,
        duplicate=duplicate,
        partial_ocr="OCR_PARTIAL_SUCCESS" in (ocr_flag_codes or []),
        known_ids=known_ids,
        suggestion_label=suggestion,
        check_specialty=check_specialty,
    )
    if "OCR_DOCUMENT_NO_TEXT" in (ocr_flag_codes or []):
        drafts.append(
            FlagDraft(
                "OCR_DOCUMENT_NO_TEXT",
                "warning",
                None,
                "The document did not contain readable text.",
            )
        )
    if extraction is not None:
        drafts.extend(_document_flags(extraction))
        drafts.extend(_grounding_flags(ground(extraction, lines or {})))
    code = run_error_code or ""
    if code == "AI_SKIPPED":
        drafts.append(
            FlagDraft(
                "ai_skipped",
                "warning",
                None,
                "Suggestions were skipped. Enter the values from the document.",
            )
        )
    elif code.startswith("AI_") or code == "INTERRUPTED":
        drafts.append(
            FlagDraft(
                "ai_failed",
                "warning",
                None,
                "Suggestions could not be produced. Enter the values from the document.",
            )
        )
    return drafts
