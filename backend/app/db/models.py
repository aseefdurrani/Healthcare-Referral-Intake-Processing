"""ORM tables for the eight intake records.

Checks, partial indexes, triggers, grants, and ``demo_purge_referral`` live in
the Alembic revisions. Actor columns store a Cognito ``sub``. There is no
staff email column and no patient table. Object keys are one folder per
referral: ``referrals/<referral-uuid>/source.<ext>`` and
``referrals/<referral-uuid>/ocr/<ocr-run-uuid>.json.gz``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    Numeric,
    SmallInteger,
    Text,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, CHAR, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Referral(Base):
    __tablename__ = "referrals"
    __table_args__ = (
        ForeignKeyConstraint(
            ["current_run_id"],
            ["processing_runs.id"],
            name="referrals_current_run_id_fkey",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["possible_duplicate_of_referral_id"],
            ["referrals.id"],
            name="referrals_possible_duplicate_fkey",
            ondelete="SET NULL",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    status: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer)
    current_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    idempotency_key: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    possible_duplicate_of_referral_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    specialty_decision: Mapped[str | None] = mapped_column(Text)
    destination_specialty_code: Mapped[str | None] = mapped_column(Text)
    destination_specialty_other: Mapped[str | None] = mapped_column(Text)
    rejection_reason_code: Mapped[str | None] = mapped_column(Text)
    rejection_note: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        ForeignKeyConstraint(
            ["referral_id"],
            ["referrals.id"],
            name="documents_referral_id_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["possible_duplicate_of_document_id"],
            ["documents.id"],
            name="documents_possible_duplicate_fkey",
            ondelete="SET NULL",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    referral_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True))
    s3_bucket: Mapped[str] = mapped_column(Text)
    s3_key: Mapped[str] = mapped_column(Text)
    s3_version_id: Mapped[str | None] = mapped_column(Text)
    media_type: Mapped[str] = mapped_column(Text)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    page_count: Mapped[int | None] = mapped_column(Integer)
    sha256_hex: Mapped[str | None] = mapped_column(CHAR(64))
    s3_etag: Mapped[str | None] = mapped_column(Text)
    sse_algorithm: Mapped[str | None] = mapped_column(Text)
    original_filename: Mapped[str] = mapped_column(Text)
    storage_status: Mapped[str] = mapped_column(Text)
    storage_error_code: Mapped[str | None] = mapped_column(Text)
    possible_duplicate_of_document_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    uploaded_by: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    stored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class ProcessingRun(Base):
    __tablename__ = "processing_runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["referral_id"],
            ["referrals.id"],
            name="processing_runs_referral_id_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["ocr_run_id"],
            ["ocr_runs.id"],
            name="processing_runs_ocr_run_id_fkey",
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    referral_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True))
    attempt: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(Text)
    trigger_kind: Mapped[str] = mapped_column(Text)
    ocr_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    request_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True))
    error_code: Mapped[str | None] = mapped_column(Text)
    retryable: Mapped[bool | None] = mapped_column(Boolean)
    failure_stage: Mapped[str | None] = mapped_column(Text)
    failure_detail: Mapped[str | None] = mapped_column(Text)
    textract_pages: Mapped[int | None] = mapped_column(Integer)
    textract_job_id: Mapped[str | None] = mapped_column(Text)
    model_id: Mapped[str | None] = mapped_column(Text)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    estimated_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    pricing_rates_as_of: Mapped[date | None] = mapped_column(Date)
    aws_request_id: Mapped[str | None] = mapped_column(Text)
    ocr_ms: Mapped[int | None] = mapped_column(Integer)
    ai_ms: Mapped[int | None] = mapped_column(Integer)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class OcrRun(Base):
    __tablename__ = "ocr_runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["document_id", "referral_id"],
            ["documents.id", "documents.referral_id"],
            name="ocr_runs_document_referral_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["created_by_run_id", "referral_id"],
            ["processing_runs.id", "processing_runs.referral_id"],
            name="ocr_runs_created_by_run_fkey",
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    document_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True))
    referral_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True))
    created_by_run_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True))
    attempt: Mapped[int] = mapped_column(Integer)
    config_version: Mapped[str] = mapped_column(Text)
    feature_types: Mapped[list[str]] = mapped_column(ARRAY(Text))
    client_request_token: Mapped[str] = mapped_column(Text)
    textract_job_id: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    error_category: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(Text)
    status_message: Mapped[str | None] = mapped_column(Text)
    preflight_page_count: Mapped[int | None] = mapped_column(Integer)
    textract_page_count: Mapped[int | None] = mapped_column(Integer)
    pages_billed_estimate: Mapped[int | None] = mapped_column(Integer)
    model_version: Mapped[str | None] = mapped_column(Text)
    normalizer_version: Mapped[str | None] = mapped_column(Text)
    raw_output_s3_key: Mapped[str | None] = mapped_column(Text)
    raw_output_sha256_hex: Mapped[str | None] = mapped_column(CHAR(64))
    raw_output_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    normalized: Mapped[Any | None] = mapped_column(JSONB)
    mean_word_confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    flag_codes: Mapped[list[str]] = mapped_column(
        ARRAY(Text),
        server_default=text("'{}'::text[]"),
    )
    poll_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class AiRun(Base):
    __tablename__ = "ai_runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["processing_run_id", "referral_id"],
            ["processing_runs.id", "processing_runs.referral_id"],
            name="ai_runs_processing_run_fkey",
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    processing_run_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True))
    referral_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True))
    status: Mapped[str] = mapped_column(Text)
    model_id: Mapped[str] = mapped_column(Text)
    prompt_version: Mapped[str] = mapped_column(Text)
    schema_version: Mapped[str] = mapped_column(Text)
    invocation_count: Mapped[int] = mapped_column(SmallInteger)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    estimated_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    stop_reason: Mapped[str | None] = mapped_column(Text)
    provider_request_id: Mapped[str | None] = mapped_column(Text)
    validated_output: Mapped[Any | None] = mapped_column(JSONB)
    raw_output_text: Mapped[str | None] = mapped_column(Text)
    specialty_label: Mapped[str | None] = mapped_column(Text)
    other_specialty_name: Mapped[str | None] = mapped_column(Text)
    relation_to_request: Mapped[str | None] = mapped_column(Text)
    rationale: Mapped[str | None] = mapped_column(Text)
    candidates: Mapped[Any] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'[]'::jsonb"),
    )
    error_code: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class ReferralField(Base):
    __tablename__ = "referral_fields"
    __table_args__ = (
        ForeignKeyConstraint(
            ["processing_run_id", "referral_id"],
            ["processing_runs.id", "processing_runs.referral_id"],
            name="referral_fields_processing_run_fkey",
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    processing_run_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True))
    referral_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True))
    field_key: Mapped[str] = mapped_column(Text)
    suggested_text: Mapped[str | None] = mapped_column(Text)
    suggested_json: Mapped[Any | None] = mapped_column(JSONB)
    ai_status: Mapped[str] = mapped_column(Text)
    evidence: Mapped[Any] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'[]'::jsonb"),
    )
    note: Mapped[str | None] = mapped_column(Text)
    confirmed_text: Mapped[str | None] = mapped_column(Text)
    confirmed_json: Mapped[Any | None] = mapped_column(JSONB)
    confirmed_absent: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_by: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class ValidationFlag(Base):
    __tablename__ = "validation_flags"
    __table_args__ = (
        ForeignKeyConstraint(
            ["referral_id"],
            ["referrals.id"],
            name="validation_flags_referral_id_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["processing_run_id"],
            ["processing_runs.id"],
            name="validation_flags_processing_run_id_fkey",
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    referral_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True))
    processing_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    code: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(Text)
    field_key: Mapped[str | None] = mapped_column(Text)
    message: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    acknowledgement_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["referral_id"],
            ["referrals.id"],
            name="audit_events_referral_id_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["document_id", "referral_id"],
            ["documents.id", "documents.referral_id"],
            name="audit_events_document_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["run_id", "referral_id"],
            ["processing_runs.id", "processing_runs.referral_id"],
            name="audit_events_run_fkey",
            ondelete="RESTRICT",
        ),
    )

    audit_event_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    actor_type: Mapped[str] = mapped_column(Text)
    actor_id: Mapped[str | None] = mapped_column(Text)
    action: Mapped[str] = mapped_column(Text)
    referral_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True))
    document_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    field_name: Mapped[str | None] = mapped_column(Text)
    old_value: Mapped[Any | None] = mapped_column(JSONB)
    new_value: Mapped[Any | None] = mapped_column(JSONB)
    from_status: Mapped[str | None] = mapped_column(Text)
    to_status: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    request_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True))
