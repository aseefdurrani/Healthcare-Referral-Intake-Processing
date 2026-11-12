"""Create a referral only after the file checks pass, then store the bytes."""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.adapters.storage import ObjectMissing, StorageError
from app.core.config import Settings
from app.core.errors import problem
from app.core.logging import document_id_var, log_event, referral_id_var, run_id_var
from app.db.models import Document, ProcessingRun, Referral
from app.schemas.referrals import ReferralSummary
from app.services.clock import Clock
from app.services.files import AcceptedFile
from app.services.keys import source_key
from app.services.objects import write_once
from app.services.persistence import add_audit, sync_flags
from app.services.present import summary_for
from app.services.runner import ProcessingRunner
from app.services.validation import FlagDraft


class IntakeResult:
    def __init__(self, summary: ReferralSummary, *, failed: bool = False) -> None:
        self.summary = summary
        self.failed = failed
        self.referral_id = summary.id


def create_referral(
    session: Session,
    *,
    settings: Settings,
    storage: object,
    runner: ProcessingRunner | None,
    clock: Clock,
    staff_sub: str,
    request_id: uuid.UUID,
    accepted: AcceptedFile,
    body: bytes,
    idempotency_key: uuid.UUID | None,
) -> IntakeResult:
    if idempotency_key is not None:
        existing = _same_key(session, idempotency_key, accepted.sha256_hex)
        if existing is not None:
            return IntakeResult(existing)
    duplicate = session.scalar(
        select(Document)
        .where(Document.sha256_hex == accepted.sha256_hex)
        .order_by(Document.created_at)
        .limit(1)
    )
    now = clock.now()
    referral_id = uuid.uuid4()
    document_id = uuid.uuid4()
    run_id = uuid.uuid4()
    key = source_key(referral_id, accepted.media_type)
    referral = Referral(
        id=referral_id,
        status="uploaded",
        version=1,
        current_run_id=None,
        idempotency_key=idempotency_key,
        possible_duplicate_of_referral_id=None if duplicate is None else duplicate.referral_id,
        created_by=staff_sub,
        updated_at=now,
    )
    document = Document(
        id=document_id,
        referral_id=referral_id,
        s3_bucket=settings.s3_bucket_name,
        s3_key=key,
        media_type=accepted.media_type,
        sha256_hex=accepted.sha256_hex,
        original_filename=accepted.filename,
        storage_status="storing",
        possible_duplicate_of_document_id=None if duplicate is None else duplicate.id,
        uploaded_by=staff_sub,
        updated_at=now,
    )
    run = ProcessingRun(
        id=run_id,
        referral_id=referral_id,
        attempt=1,
        status="queued",
        trigger_kind="upload",
        request_id=request_id,
        updated_at=now,
    )
    session.add(referral)
    session.flush()
    session.add(document)
    session.add(run)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        if idempotency_key is None:
            raise
        raced = _same_key(session, idempotency_key, accepted.sha256_hex)
        if raced is None:
            raise problem("IDEMPOTENCY_KEY_REUSED") from None
        return IntakeResult(raced)
    referral.current_run_id = run.id
    add_audit(
        session,
        action="uploaded",
        referral_id=referral.id,
        request_id=request_id,
        actor_type="staff",
        actor_id=staff_sub,
        document_id=document.id,
        run_id=run.id,
        to_status="uploaded",
    )
    if duplicate is not None:
        sync_flags(
            session,
            referral_id=referral.id,
            run_id=run.id,
            desired=[
                FlagDraft(
                    "DUPLICATE_DOCUMENT",
                    "warning",
                    None,
                    "This file matches an earlier referral.",
                )
            ],
            now=now,
        )
    session.commit()
    session.refresh(referral)
    referral_id_var.set(str(referral.id))
    document_id_var.set(str(document.id))
    run_id_var.set(str(run.id))
    log_event(
        logging.INFO,
        "referral.upload.received",
        content_type=accepted.media_type,
        size_bytes=accepted.size_bytes,
        actor_id=staff_sub,
    )
    try:
        head = write_once(
            storage,  # type: ignore[arg-type]
            key=key,
            body=body,
            content_type=accepted.media_type,
            sha256=accepted.sha256,
            sha256_hex=accepted.sha256_hex,
        )
    except (StorageError, ObjectMissing) as exc:
        code = exc.code if isinstance(exc, StorageError) else "s3_unavailable"
        _storage_failed(session, referral, document, run, code, clock)
        raise problem(
            "DEPENDENCY_UNAVAILABLE",
            extra={"referral_id": str(referral.id)},
        ) from exc
    stored = clock.now()
    document.storage_status = "stored"
    document.size_bytes = accepted.size_bytes
    document.page_count = accepted.page_count
    document.s3_etag = head.etag or "stored"
    document.sse_algorithm = "aws:kms"
    document.stored_at = stored
    document.updated_at = stored
    session.commit()
    log_event(
        logging.INFO,
        "document.stored",
        object_key=key,
        duration_ms=0,
        aws_request_id=head.aws_request_id,
    )
    if runner is not None:
        runner.submit(run.id)
    return IntakeResult(summary_for(session, referral))


def _same_key(session: Session, key: uuid.UUID, sha256_hex: str) -> ReferralSummary | None:
    referral = session.scalar(select(Referral).where(Referral.idempotency_key == key))
    if referral is None:
        return None
    document = session.scalar(select(Document).where(Document.referral_id == referral.id))
    stored = "" if document is None or document.sha256_hex is None else document.sha256_hex.strip()
    if stored != sha256_hex:
        raise problem("IDEMPOTENCY_KEY_REUSED")
    return summary_for(session, referral)


def _storage_failed(
    session: Session,
    referral: Referral,
    document: Document,
    run: ProcessingRun,
    code: str,
    clock: Clock,
) -> None:
    now = clock.now()
    document.storage_status = "storage_failed"
    document.storage_error_code = code
    document.updated_at = now
    previous = referral.status
    referral.status = "failed"
    referral.version += 1
    referral.updated_at = now
    run.status = "failed"
    run.error_code = "STORAGE_FAILED"
    run.retryable = False
    run.failure_stage = "other"
    run.failure_detail = "storage"
    run.finished_at = now
    run.updated_at = now
    add_audit(
        session,
        action="processing_failed",
        referral_id=referral.id,
        request_id=run.request_id,
        actor_type="system",
        document_id=document.id,
        run_id=run.id,
        from_status=previous,
        to_status="failed",
    )
    session.commit()
    log_event(
        logging.ERROR,
        "run.failed",
        stage="other",
        failure_detail="storage",
        error_code="STORAGE_FAILED",
        retryable=False,
        attempt=run.attempt,
        total_duration_ms=0,
        estimated_cost_usd=0,
    )


def reconcile_storing(
    session: Session,
    *,
    storage: object,
    runner: ProcessingRunner | None,
    clock: Clock,
    older_than_seconds: int = 60,
) -> None:
    cutoff = clock.now() - timedelta(seconds=older_than_seconds)
    documents = list(session.scalars(select(Document).where(Document.storage_status == "storing")))
    for document in documents:
        if document.created_at > cutoff:
            continue
        referral = session.get(Referral, document.referral_id)
        run = (
            session.get(ProcessingRun, referral.current_run_id)
            if referral and referral.current_run_id
            else None
        )
        try:
            head = storage.head(document.s3_key)  # type: ignore[attr-defined]
        except ObjectMissing:
            if referral is not None and run is not None:
                _storage_failed(session, referral, document, run, "reconcile_missing_object", clock)
            continue
        except StorageError as exc:
            if exc.code == "s3_access_denied":
                log_event(
                    logging.ERROR,
                    "app.error.unhandled",
                    exception_type="AccessDenied",
                    error_code="INTERNAL_ERROR",
                )
            continue
        from app.adapters.storage import checksum_matches, encryption_ok

        sha = (document.sha256_hex or "").strip()
        if not checksum_matches(sha, head.checksum_sha256) or not encryption_ok(head):
            code = "s3_encryption_mismatch" if not encryption_ok(head) else "s3_checksum_mismatch"
            if referral is not None and run is not None:
                _storage_failed(session, referral, document, run, code, clock)
            continue
        body = storage.get_bytes(document.s3_key)  # type: ignore[attr-defined]
        from app.services.files import inspect_upload

        try:
            accepted = inspect_upload(
                body, document.original_filename, max_bytes=10_000_000, max_pages=10
            )
        except Exception:
            if referral is not None and run is not None:
                _storage_failed(session, referral, document, run, "s3_checksum_mismatch", clock)
            continue
        now = clock.now()
        document.storage_status = "stored"
        document.size_bytes = accepted.size_bytes
        document.page_count = accepted.page_count
        document.s3_etag = head.etag or "stored"
        document.sse_algorithm = "aws:kms"
        document.stored_at = now
        document.updated_at = now
        session.commit()
        if runner is not None and run is not None and run.status == "queued":
            runner.submit(run.id)
