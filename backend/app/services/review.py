"""Review reads and the staff transitions."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.adapters.storage import ObjectMissing, StorageError
from app.core.config import Settings
from app.core.errors import problem
from app.core.logging import log_event
from app.db.models import (
    AiRun,
    Document,
    OcrRun,
    ProcessingRun,
    Referral,
    ReferralField,
    ValidationFlag,
)
from app.schemas.referrals import (
    ApproveRequest,
    ReferralDetail,
    ReferralPage,
    ReferralPatch,
    ReferralStatusOut,
    RejectRequest,
)
from app.services.clock import Clock, SystemClock
from app.services.fields import view_from_row
from app.services.flags import assemble_flags
from app.services.ocr_document import element_index, line_map
from app.services.persistence import add_audit, sync_flags
from app.services.present import detail_for, status_for, summary_for
from app.services.runner import ProcessingRunner
from app.services.taxonomy import ACK_FLAG_CODES, DESTINATIONS, KNOWN_FIELDS
from app.services.validation import effective_text
from app.services.workflow import ai_stage_failure, allowed_actions

_CONTACT = ("patient.phone", "patient.email", "patient.address")
_CLINICIAN_REACH = ("referring_clinician.phone", "referring_clinician.fax")


def _referral(session: Session, referral_id: uuid.UUID) -> Referral:
    referral = session.get(Referral, referral_id)
    if referral is None:
        raise problem("REFERRAL_NOT_FOUND")
    return referral


def _lock(session: Session, referral_id: uuid.UUID, version: int) -> Referral:
    referral = session.scalar(select(Referral).where(Referral.id == referral_id).with_for_update())
    if referral is None:
        raise problem("REFERRAL_NOT_FOUND")
    if referral.version != version:
        raise problem("VERSION_CONFLICT")
    return referral


def _attempts(session: Session, referral_id: uuid.UUID) -> int:
    value = session.scalar(
        select(func.count())
        .select_from(ProcessingRun)
        .where(ProcessingRun.referral_id == referral_id)
    )
    return int(value or 0)


def _actions(session: Session, referral: Referral, settings: Settings) -> list[str]:
    run = session.get(ProcessingRun, referral.current_run_id) if referral.current_run_id else None
    return allowed_actions(
        referral,
        run,
        attempt_count=_attempts(session, referral.id),
        max_attempts=settings.processing_max_attempts,
    )


def list_referrals(
    session: Session,
    *,
    statuses: list[str] | None,
    limit: int,
    offset: int,
) -> ReferralPage:
    stmt = select(Referral).order_by(Referral.created_at.desc(), Referral.id.desc())
    count_stmt = select(func.count()).select_from(Referral)
    if statuses:
        stmt = stmt.where(Referral.status.in_(statuses))
        count_stmt = count_stmt.where(Referral.status.in_(statuses))
    total = int(session.scalar(count_stmt) or 0)
    rows = list(session.scalars(stmt.limit(limit).offset(offset)))
    return ReferralPage(
        items=[summary_for(session, row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


def read_status(
    session: Session,
    referral_id: uuid.UUID,
    runner: ProcessingRunner | None,
) -> ReferralStatusOut:
    referral = _referral(session, referral_id)
    if (
        referral.status == "processing"
        and runner is not None
        and referral.current_run_id is not None
    ):
        runner.submit(referral.current_run_id)
    return status_for(session, referral)


def read_referral(
    session: Session,
    referral_id: uuid.UUID,
    *,
    settings: Settings,
    staff_sub: str,
    request_id: uuid.UUID,
    clock: Clock | None = None,
) -> ReferralDetail:
    referral = _referral(session, referral_id)
    document = session.scalar(select(Document).where(Document.referral_id == referral.id))
    now = (clock or SystemClock()).now()
    event = add_audit(
        session,
        action="referral_viewed",
        referral_id=referral.id,
        request_id=request_id,
        actor_type="staff",
        actor_id=staff_sub,
        document_id=None if document is None else document.id,
        run_id=referral.current_run_id,
    )
    session.commit()
    _log_review(event.audit_event_id, "referral_viewed", staff_sub, None)
    _ = now
    return detail_for(session, referral, actions=_actions(session, referral, settings))


def read_document(
    session: Session,
    referral_id: uuid.UUID,
    *,
    storage: Any,
    staff_sub: str,
    request_id: uuid.UUID,
) -> tuple[bytes, str, str]:
    referral = _referral(session, referral_id)
    document = session.scalar(select(Document).where(Document.referral_id == referral.id))
    if document is None or document.storage_status != "stored":
        raise problem("DEPENDENCY_UNAVAILABLE")
    try:
        body = storage.get_bytes(document.s3_key)
    except (StorageError, ObjectMissing) as exc:
        raise problem("DEPENDENCY_UNAVAILABLE") from exc
    event = add_audit(
        session,
        action="document_viewed",
        referral_id=referral.id,
        request_id=request_id,
        actor_type="staff",
        actor_id=staff_sub,
        document_id=document.id,
        run_id=referral.current_run_id,
    )
    session.commit()
    _log_review(event.audit_event_id, "document_viewed", staff_sub, None)
    extension = document.s3_key.rsplit(".", 1)[-1]
    filename = f"referral-{referral.id.hex[:8]}.{extension}"
    return body, document.media_type, filename


def patch_referral(
    session: Session,
    referral_id: uuid.UUID,
    body: ReferralPatch,
    *,
    settings: Settings,
    staff_sub: str,
    request_id: uuid.UUID,
    clock: Clock | None = None,
) -> ReferralDetail:
    now = (clock or SystemClock()).now()
    referral = _lock(session, referral_id, body.version)
    if referral.status != "needs_review":
        raise problem("INVALID_TRANSITION")
    run = session.get(ProcessingRun, referral.current_run_id) if referral.current_run_id else None
    if run is None:
        raise problem("INVALID_TRANSITION")
    rows = {
        row.field_key: row
        for row in session.scalars(
            select(ReferralField).where(ReferralField.processing_run_id == run.id)
        )
    }
    for item in body.fields:
        if item.key not in KNOWN_FIELDS:
            raise problem("UNKNOWN_FIELD")
    document = session.scalar(select(Document).where(Document.referral_id == referral.id))
    events = []
    for item in body.fields:
        row = rows.get(item.key)
        if row is None:
            row = ReferralField(
                id=uuid.uuid4(),
                processing_run_id=run.id,
                referral_id=referral.id,
                field_key=item.key,
                ai_status="not_found",
                evidence=[],
                confirmed_absent=False,
                updated_at=now,
            )
            session.add(row)
            session.flush()
            rows[item.key] = row
        old = {"text": row.confirmed_text, "absent": row.confirmed_absent}
        if item.value:
            row.confirmed_text = item.value
            row.confirmed_absent = False
        else:
            row.confirmed_text = None
            row.confirmed_absent = True
        row.confirmed_at = now
        row.confirmed_by = staff_sub
        row.updated_at = now
        new = {"text": row.confirmed_text, "absent": row.confirmed_absent}
        events.append(
            add_audit(
                session,
                action="field_corrected",
                referral_id=referral.id,
                request_id=request_id,
                actor_type="staff",
                actor_id=staff_sub,
                document_id=None if document is None else document.id,
                run_id=run.id,
                field_name=item.key,
                old_value=old,
                new_value=new,
                from_status="needs_review",
                to_status="needs_review",
            )
        )
    _revalidate(session, referral, run, now)
    referral.version += 1
    referral.updated_at = now
    session.commit()
    for event in events:
        _log_review(event.audit_event_id, "field_corrected", staff_sub, event.field_name)
    return detail_for(session, referral, actions=_actions(session, referral, settings))


def approve_referral(
    session: Session,
    referral_id: uuid.UUID,
    body: ApproveRequest,
    *,
    settings: Settings,
    staff_sub: str,
    request_id: uuid.UUID,
    clock: Clock | None = None,
) -> ReferralDetail:
    if body.destination_specialty.code not in DESTINATIONS:
        raise problem("UNKNOWN_SPECIALTY")
    now = (clock or SystemClock()).now()
    referral = _lock(session, referral_id, body.version)
    if referral.status != "needs_review":
        raise problem("INVALID_TRANSITION")
    run = session.get(ProcessingRun, referral.current_run_id) if referral.current_run_id else None
    if run is None:
        raise problem("INVALID_TRANSITION")
    rows = {
        row.field_key: row
        for row in session.scalars(
            select(ReferralField).where(ReferralField.processing_run_id == run.id)
        )
    }
    for key in body.not_on_document:
        row = rows.get(key)
        if row is None:
            continue
        row.confirmed_text = None
        row.confirmed_json = None
        row.confirmed_absent = True
        row.confirmed_at = now
        row.confirmed_by = staff_sub
        row.updated_at = now
    priority = rows.get("referral.sender_priority")
    if priority is not None:
        priority.confirmed_text = body.sender_priority
        priority.confirmed_absent = False
        priority.confirmed_at = now
        priority.confirmed_by = staff_sub
        priority.updated_at = now
    _revalidate(session, referral, run, now)
    session.flush()
    flags = list(
        session.scalars(select(ValidationFlag).where(ValidationFlag.referral_id == referral.id))
    )
    by_id = {flag.id: flag for flag in flags}
    for item in body.acknowledgements:
        if item.flag_id not in by_id:
            raise problem("UNKNOWN_FLAG")
    blockers = _blockers(rows, flags, body)
    if blockers:
        session.rollback()
        raise problem("UNRESOLVED_BLOCKING_FLAGS", errors=blockers)
    for item in body.acknowledgements:
        flag = by_id[item.flag_id]
        flag.status = "acknowledged"
        flag.acknowledgement_reason = item.reason.strip()
        flag.acknowledged_at = now
        flag.updated_at = now
    ai = session.scalar(select(AiRun).where(AiRun.processing_run_id == run.id))
    suggested = ai.specialty_label if ai is not None and ai.status == "succeeded" else None
    decision = "accept_suggested" if body.destination_specialty.code == suggested else "override"
    previous = referral.status
    referral.status = "approved"
    referral.version += 1
    referral.specialty_decision = decision
    referral.destination_specialty_code = body.destination_specialty.code
    referral.destination_specialty_other = (
        body.destination_specialty.other_name.strip()
        if body.destination_specialty.other_name
        else None
    )
    referral.updated_at = now
    document = session.scalar(select(Document).where(Document.referral_id == referral.id))
    if decision == "override":
        add_audit(
            session,
            action="specialty_changed",
            referral_id=referral.id,
            request_id=request_id,
            actor_type="staff",
            actor_id=staff_sub,
            document_id=None if document is None else document.id,
            run_id=run.id,
            old_value={"label": suggested},
            new_value={
                "code": referral.destination_specialty_code,
                "other_name": referral.destination_specialty_other,
            },
        )
    event = add_audit(
        session,
        action="approved",
        referral_id=referral.id,
        request_id=request_id,
        actor_type="staff",
        actor_id=staff_sub,
        document_id=None if document is None else document.id,
        run_id=run.id,
        from_status=previous,
        to_status="approved",
    )
    session.commit()
    _log_review(event.audit_event_id, "approved", staff_sub, None)
    return detail_for(session, referral, actions=_actions(session, referral, settings))


def reject_referral(
    session: Session,
    referral_id: uuid.UUID,
    body: RejectRequest,
    *,
    settings: Settings,
    staff_sub: str,
    request_id: uuid.UUID,
    clock: Clock | None = None,
) -> ReferralDetail:
    now = (clock or SystemClock()).now()
    referral = _lock(session, referral_id, body.version)
    if referral.status not in {"needs_review", "failed"}:
        raise problem("INVALID_TRANSITION")
    previous = referral.status
    referral.status = "rejected"
    referral.version += 1
    referral.rejection_reason_code = body.reason_code
    referral.rejection_note = body.note.strip() if body.note else None
    referral.specialty_decision = None
    referral.updated_at = now
    document = session.scalar(select(Document).where(Document.referral_id == referral.id))
    event = add_audit(
        session,
        action="rejected",
        referral_id=referral.id,
        request_id=request_id,
        actor_type="staff",
        actor_id=staff_sub,
        document_id=None if document is None else document.id,
        run_id=referral.current_run_id,
        from_status=previous,
        to_status="rejected",
        reason=body.reason_code,
    )
    session.commit()
    _log_review(event.audit_event_id, "rejected", staff_sub, None)
    return detail_for(session, referral, actions=_actions(session, referral, settings))


def retry_referral(
    session: Session,
    referral_id: uuid.UUID,
    version: int,
    *,
    settings: Settings,
    runner: ProcessingRunner | None,
    staff_sub: str,
    request_id: uuid.UUID,
    clock: Clock | None = None,
) -> ReferralDetail:
    now = (clock or SystemClock()).now()
    referral = _lock(session, referral_id, version)
    run = session.get(ProcessingRun, referral.current_run_id) if referral.current_run_id else None
    attempts = _attempts(session, referral.id)
    if attempts >= settings.processing_max_attempts:
        raise problem("RETRY_LIMIT_REACHED")
    if referral.status == "failed":
        if run is None or not run.retryable:
            raise problem("NOT_RETRYABLE")
    elif referral.status == "needs_review" and ai_stage_failure(run):
        if run is not None and run.retryable is False:
            raise problem("NOT_RETRYABLE")
    else:
        raise problem("INVALID_TRANSITION")
    reuse = None
    if run is not None and ai_stage_failure(run) and run.ocr_run_id is not None:
        ocr = session.get(OcrRun, run.ocr_run_id)
        if ocr is not None and ocr.status == "succeeded":
            reuse = ocr.id
    new_run = ProcessingRun(
        id=uuid.uuid4(),
        referral_id=referral.id,
        attempt=attempts + 1,
        status="queued",
        trigger_kind="retry",
        ocr_run_id=reuse,
        request_id=request_id,
        textract_job_id=None,
        updated_at=now,
    )
    session.add(new_run)
    session.flush()
    if reuse is not None and run is not None:
        previous_ocr = session.get(OcrRun, reuse)
        if previous_ocr is not None:
            new_run.textract_job_id = previous_ocr.textract_job_id
    previous = referral.status
    referral.status = "processing"
    referral.version += 1
    referral.current_run_id = new_run.id
    referral.updated_at = now
    document = session.scalar(select(Document).where(Document.referral_id == referral.id))
    event = add_audit(
        session,
        action="reprocess_requested",
        referral_id=referral.id,
        request_id=request_id,
        actor_type="staff",
        actor_id=staff_sub,
        document_id=None if document is None else document.id,
        run_id=new_run.id,
        from_status=previous,
        to_status="processing",
    )
    session.commit()
    log_event(
        logging.INFO,
        "run.retry_scheduled",
        next_attempt=new_run.attempt,
        trigger="retry",
        reason=run.error_code if run is not None else None,
        delay_ms=0,
    )
    _log_review(event.audit_event_id, "reprocess_requested", staff_sub, None)
    if runner is not None:
        runner.submit(new_run.id)
    return detail_for(session, referral, actions=_actions(session, referral, settings))


def _revalidate(session: Session, referral: Referral, run: ProcessingRun, now: Any) -> None:
    rows = list(
        session.scalars(select(ReferralField).where(ReferralField.processing_run_id == run.id))
    )
    ocr = session.get(OcrRun, run.ocr_run_id) if run.ocr_run_id else None
    normalized = ocr.normalized if ocr is not None and isinstance(ocr.normalized, dict) else {}
    ai = session.scalar(select(AiRun).where(AiRun.processing_run_id == run.id))
    extraction = (
        ai.validated_output if ai is not None and isinstance(ai.validated_output, dict) else None
    )
    sync_flags(
        session,
        referral_id=referral.id,
        run_id=run.id,
        desired=assemble_flags(
            [view_from_row(row) for row in rows],
            today=now.date(),
            duplicate=referral.possible_duplicate_of_referral_id is not None,
            ocr_flag_codes=list(ocr.flag_codes or []) if ocr is not None else [],
            known_ids=set(element_index(normalized)) if normalized else None,
            extraction=extraction,
            lines=line_map(normalized) if normalized else {},
            run_error_code=run.error_code,
        ),
        now=now,
    )


def _blockers(
    rows: dict[str, ReferralField],
    flags: list[ValidationFlag],
    body: ApproveRequest,
) -> list[dict[str, object]]:
    views = {key: view_from_row(row) for key, row in rows.items()}

    def text_of(key: str) -> str | None:
        view = views.get(key)
        return None if view is None else effective_text(view)

    def absent(key: str) -> bool:
        view = views.get(key)
        return view is not None and view.confirmed_absent

    errors: list[dict[str, object]] = []

    def add(loc: str) -> None:
        errors.append({"loc": ["body", loc], "msg": "Approval requirement is not met."})

    if not text_of("patient.full_name"):
        add("patient.full_name")
    dob = text_of("patient.date_of_birth")
    if not dob:
        add("patient.date_of_birth")
    else:
        from datetime import date

        try:
            parsed = date.fromisoformat(dob)
            today = date.today()
            years = (
                today.year - parsed.year - ((today.month, today.day) < (parsed.month, parsed.day))
            )
            if parsed > today or years > 120:
                add("patient.date_of_birth")
        except ValueError:
            add("patient.date_of_birth")
    if not any(text_of(key) for key in _CONTACT) and not all(absent(key) for key in _CONTACT):
        add("patient.phone")
    if not text_of("referring_clinician.name") and not absent("referring_clinician.name"):
        add("referring_clinician.name")
    if not any(text_of(key) for key in _CLINICIAN_REACH) and not all(
        absent(key) for key in _CLINICIAN_REACH
    ):
        add("referring_clinician.phone")
    if not text_of("referral.reason") and not absent("referral.reason"):
        add("referral.reason")
    for key, view in views.items():
        if view.ai_status == "conflicting" and not (
            view.confirmed_text and view.confirmed_text.strip()
        ):
            add(key)
    acknowledged = {item.flag_id for item in body.acknowledgements}
    for flag in flags:
        if flag.status == "open" and flag.code in ACK_FLAG_CODES and flag.id not in acknowledged:
            add(flag.code)
    return errors


def _log_review(event_id: uuid.UUID, action: str, actor_id: str, field_name: str | None) -> None:
    fields: dict[str, object] = {
        "audit_event_id": str(event_id),
        "action": action,
        "actor_id": actor_id,
    }
    if field_name is not None:
        fields["field_name"] = field_name
    log_event(logging.INFO, "review.action", **fields)
