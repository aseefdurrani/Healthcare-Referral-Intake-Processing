"""Shared row writes for audits and flags."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import null, select
from sqlalchemy.orm import Session

from app.db.models import AuditEvent, ValidationFlag
from app.services.validation import FlagDraft


def add_audit(
    session: Session,
    *,
    action: str,
    referral_id: uuid.UUID,
    request_id: uuid.UUID,
    actor_type: str,
    actor_id: str | None = None,
    document_id: uuid.UUID | None = None,
    run_id: uuid.UUID | None = None,
    field_name: str | None = None,
    old_value: Any = None,
    new_value: Any = None,
    from_status: str | None = None,
    to_status: str | None = None,
    reason: str | None = None,
) -> AuditEvent:
    event = AuditEvent(
        audit_event_id=uuid.uuid4(),
        actor_type=actor_type,
        actor_id=actor_id,
        action=action,
        referral_id=referral_id,
        document_id=document_id,
        run_id=run_id,
        field_name=field_name,
        old_value=null() if old_value is None else old_value,
        new_value=null() if new_value is None else new_value,
        from_status=from_status,
        to_status=to_status,
        reason=reason,
        request_id=request_id,
    )
    session.add(event)
    session.flush()
    return event


def sync_flags(
    session: Session,
    *,
    referral_id: uuid.UUID,
    run_id: uuid.UUID | None,
    desired: list[FlagDraft],
    now: datetime,
) -> None:
    existing = list(
        session.scalars(select(ValidationFlag).where(ValidationFlag.referral_id == referral_id))
    )
    by_key = {(row.code, row.field_key or ""): row for row in existing}
    seen: set[tuple[str, str]] = set()
    for draft in desired:
        key = (draft.code, draft.field_key or "")
        if key in seen:
            continue
        seen.add(key)
        row = by_key.get(key)
        if row is None:
            session.add(
                ValidationFlag(
                    id=uuid.uuid4(),
                    referral_id=referral_id,
                    processing_run_id=run_id,
                    code=draft.code,
                    severity=draft.severity,
                    field_key=draft.field_key,
                    message=draft.message,
                    status="open",
                    updated_at=now,
                )
            )
            continue
        if row.status == "acknowledged":
            continue
        row.status = "open"
        row.severity = draft.severity
        row.message = draft.message
        row.processing_run_id = run_id
        row.updated_at = now
    for key, row in by_key.items():
        if key not in seen and row.status == "open":
            row.status = "resolved"
            row.updated_at = now
