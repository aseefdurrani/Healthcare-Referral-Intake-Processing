"""Build the referral payloads the routes return."""

from __future__ import annotations

import uuid
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import (
    AiRun,
    AuditEvent,
    Document,
    OcrRun,
    ProcessingRun,
    Referral,
    ReferralField,
    ValidationFlag,
)
from app.schemas.referrals import (
    BoundingBox,
    DestinationSpecialty,
    DocumentOut,
    FailureSummary,
    ProcessingRunOut,
    ReferralDetail,
    ReferralEventOut,
    ReferralFieldOut,
    ReferralStatusOut,
    ReferralSummary,
    SourceRef,
    SpecialtyCandidateOut,
    SpecialtySuggestionOut,
    ValidationFlagOut,
)
from app.services.fields import view_from_row
from app.services.taxonomy import IDENTITY_FIELDS, field_label, specialty_label
from app.services.validation import effective_text

_FAILURES = {
    "STORAGE_FAILED": "The document could not be stored.",
    "OCR_FAILED": "The document could not be read.",
    "OCR_JOB_EXPIRED": "The document reading job expired.",
    "NO_TEXT_FOUND": "The document did not contain readable text.",
    "AI_FAILED": "Suggestions could not be produced.",
    "AI_THROTTLED": "Suggestions were delayed. You can try again.",
    "AI_OUTPUT_INVALID": "Suggestions could not be read.",
    "INTERRUPTED": "Suggestions were interrupted. You can try again.",
    "AI_SKIPPED": "Suggestions were skipped.",
    "INTERNAL_ERROR": "Processing could not be completed.",
}


def _document(session: Session, referral_id: uuid.UUID) -> Document:
    document = session.scalar(select(Document).where(Document.referral_id == referral_id))
    if document is None:
        raise LookupError(referral_id)
    return document


def _run(session: Session, referral: Referral) -> ProcessingRun | None:
    if referral.current_run_id is None:
        return None
    return session.get(ProcessingRun, referral.current_run_id)


def _fields(session: Session, run_id: uuid.UUID | None) -> list[ReferralField]:
    if run_id is None:
        return []
    from app.db.constants import FIELD_KEYS

    rows = list(
        session.scalars(select(ReferralField).where(ReferralField.processing_run_id == run_id))
    )
    rank = {key: index for index, key in enumerate(FIELD_KEYS)}
    return sorted(rows, key=lambda row: rank.get(row.field_key, 100))


def _flags(session: Session, referral_id: uuid.UUID) -> list[ValidationFlag]:
    return list(
        session.scalars(select(ValidationFlag).where(ValidationFlag.referral_id == referral_id))
    )


def _ai(session: Session, run_id: uuid.UUID | None) -> AiRun | None:
    if run_id is None:
        return None
    return session.scalar(select(AiRun).where(AiRun.processing_run_id == run_id))


def _patient_name(rows: list[ReferralField]) -> str | None:
    for row in rows:
        if row.field_key == "patient.full_name":
            return effective_text(view_from_row(row))
    return None


def _bbox(value: object) -> BoundingBox | None:
    if not isinstance(value, list) or len(value) != 4:
        return None
    try:
        left, top, width, height = (float(part) for part in value)
    except TypeError, ValueError:
        return None
    return BoundingBox(left=left, top=top, width=width, height=height)


def _readability(row: ReferralField) -> str | None:
    evidence = row.evidence if isinstance(row.evidence, list) else []
    scores = [
        float(item["confidence"])
        for item in evidence
        if isinstance(item, dict) and isinstance(item.get("confidence"), int | float)
    ]
    if not scores:
        return None
    score = min(scores)
    if score < 60 and row.field_key in IDENTITY_FIELDS:
        return "unreadable"
    if score < 90:
        return "verify"
    return None


def _source(row: ReferralField, readability: str | None) -> tuple[str | None, SourceRef | None]:
    evidence = (
        [item for item in row.evidence if isinstance(item, dict)]
        if isinstance(row.evidence, list)
        else []
    )
    if not evidence:
        return None, None
    first = evidence[0]
    box = _bbox(first.get("bbox"))
    page = first.get("page")
    source = None
    if box is not None and isinstance(page, int):
        source = SourceRef(
            page=page,
            element_ids=[str(item.get("line_id")) for item in evidence if item.get("line_id")],
            bbox=box,
        )
    quote = None if readability == "unreadable" else first.get("quote")
    return (quote if isinstance(quote, str) and quote else None), source


def field_out(row: ReferralField) -> ReferralFieldOut:
    view = view_from_row(row)
    readability = _readability(row)
    ocr_value, source = _source(row, readability if isinstance(readability, str) else None)
    return ReferralFieldOut(
        key=row.field_key,
        label=field_label(row.field_key),
        ocr_value=ocr_value,
        suggested_value=row.suggested_text,
        confirmed_value=row.confirmed_text,
        effective_value=effective_text(view),
        field_status=row.ai_status,  # type: ignore[arg-type]
        readability=readability,  # type: ignore[arg-type]
        source=source,
        edited=row.confirmed_text is not None or row.confirmed_absent,
    )


def _specialty(ai: AiRun | None) -> SpecialtySuggestionOut | None:
    if ai is None or ai.status != "succeeded" or not ai.specialty_label:
        return None
    candidates = []
    raw = ai.candidates if isinstance(ai.candidates, list) else []
    for item in raw[:2]:
        if isinstance(item, dict) and isinstance(item.get("label"), str):
            candidates.append(
                SpecialtyCandidateOut(label=item["label"], reason=str(item.get("reason") or ""))
            )
    return SpecialtySuggestionOut(
        label=ai.specialty_label,
        display_name=specialty_label(ai.specialty_label),
        rationale=ai.rationale,
        candidates=candidates,
    )


def _destination(referral: Referral) -> DestinationSpecialty | None:
    if referral.destination_specialty_code is None:
        return None
    return DestinationSpecialty(
        code=referral.destination_specialty_code,
        other_name=referral.destination_specialty_other,
    )


def summary_for(
    session: Session,
    referral: Referral,
    *,
    document: Document | None = None,
    rows: list[ReferralField] | None = None,
    flags: list[ValidationFlag] | None = None,
) -> ReferralSummary:
    document = document or _document(session, referral.id)
    rows = rows if rows is not None else _fields(session, referral.current_run_id)
    flags = flags if flags is not None else _flags(session, referral.id)
    ai = _ai(session, referral.current_run_id)
    open_flags = [flag for flag in flags if flag.status == "open"]
    return ReferralSummary(
        id=referral.id,
        status=referral.status,  # type: ignore[arg-type]
        version=referral.version,
        created_at=referral.created_at,
        updated_at=referral.updated_at,
        document=DocumentOut(
            id=document.id,
            display_filename=document.original_filename,
            content_type=document.media_type,
            size_bytes=document.size_bytes,
            page_count=document.page_count,
        ),
        patient_name=_patient_name(rows),
        suggested_specialty=ai.specialty_label
        if ai is not None and ai.status == "succeeded"
        else None,
        blocking_flag_count=sum(1 for flag in open_flags if flag.severity == "blocking"),
        warning_flag_count=sum(1 for flag in open_flags if flag.severity == "warning"),
        possible_duplicate_of=referral.possible_duplicate_of_referral_id,
    )


def detail_for(session: Session, referral: Referral, *, actions: list[str]) -> ReferralDetail:
    document = _document(session, referral.id)
    rows = _fields(session, referral.current_run_id)
    flags = _flags(session, referral.id)
    base = summary_for(session, referral, document=document, rows=rows, flags=flags)
    run = _run(session, referral)
    history = list(
        session.scalars(
            select(AuditEvent)
            .where(AuditEvent.referral_id == referral.id)
            .order_by(AuditEvent.occurred_at, AuditEvent.audit_event_id)
        )
    )
    return ReferralDetail(
        **base.model_dump(),
        allowed_actions=actions,  # type: ignore[arg-type]
        fields=[field_out(row) for row in rows],
        flags=[
            ValidationFlagOut(
                id=flag.id,
                code=flag.code,
                severity=flag.severity,  # type: ignore[arg-type]
                field_key=flag.field_key,
                message=flag.message,
                status=flag.status,  # type: ignore[arg-type]
                acknowledgement_reason=flag.acknowledgement_reason,
            )
            for flag in flags
        ],
        specialty=_specialty(_ai(session, referral.current_run_id)),
        specialty_decision=referral.specialty_decision,  # type: ignore[arg-type]
        destination_specialty=_destination(referral),
        current_run=None
        if run is None
        else ProcessingRunOut(
            id=run.id,
            attempt=run.attempt,
            state=run.status,  # type: ignore[arg-type]
            error_code=run.error_code,
            retryable=run.retryable,
        ),
        history=[
            ReferralEventOut(
                action=event.action,
                actor=event.actor_type,  # type: ignore[arg-type]
                from_status=event.from_status,  # type: ignore[arg-type]
                to_status=event.to_status,  # type: ignore[arg-type]
                at=event.occurred_at,
                reason=event.reason,
            )
            for event in history
        ],
    )


def status_for(session: Session, referral: Referral) -> ReferralStatusOut:
    run = _run(session, referral)
    ocr = session.get(OcrRun, run.ocr_run_id) if run is not None and run.ocr_run_id else None
    ai = _ai(session, referral.current_run_id)
    step = _step(referral, ocr, ai)
    failure = None
    if run is not None and run.error_code and referral.status in {"failed", "needs_review"}:
        failure = FailureSummary(
            step=_failure_step(run.failure_stage),
            message=_FAILURES.get(run.error_code, "Processing could not be completed."),
            retryable=bool(run.retryable),
            run_id=run.id,
        )
    return ReferralStatusOut(
        id=referral.id,
        status=referral.status,  # type: ignore[arg-type]
        version=referral.version,
        processing_step=step,  # type: ignore[arg-type]
        failure=failure,
    )


def _step(referral: Referral, ocr: OcrRun | None, ai: AiRun | None) -> str | None:
    if referral.status != "processing":
        return None
    if ocr is None or ocr.status in {"queued", "running", "timed_out"}:
        return "reading_text"
    if ai is None or ai.status in {"queued", "running"}:
        return "structuring"
    return "validating"


def _failure_step(
    stage: str | None,
) -> Literal["reading_text", "structuring", "validating"] | None:
    if stage == "ai":
        return "structuring"
    if stage == "ocr":
        return "reading_text"
    if stage == "other":
        return "reading_text"
    return None


def count_referrals(session: Session, statuses: list[str] | None) -> int:
    stmt = select(func.count()).select_from(Referral)
    if statuses:
        stmt = stmt.where(Referral.status.in_(statuses))
    value = session.scalar(stmt)
    return int(value or 0)
