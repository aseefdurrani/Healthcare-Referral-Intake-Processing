"""OCR, model, validation, and the guarded write that opens review."""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.llm import AiRunResult, bedrock_token_cost, map_ai_error
from app.adapters.ocr import JOB_TTL, OcrCollect, TextractFailure, client_request_token
from app.adapters.storage import ObjectMissing, StorageError
from app.core.config import Settings
from app.core.logging import (
    document_id_var,
    log_event,
    referral_id_var,
    request_id_var,
    run_id_var,
)
from app.db.constants import FIELD_KEYS
from app.db.models import AiRun, Document, OcrRun, ProcessingRun, Referral, ReferralField
from app.services.clock import Clock
from app.services.fields import MappedField, map_extraction, view_from_row
from app.services.flags import assemble_flags
from app.services.keys import ocr_key
from app.services.objects import write_once
from app.services.ocr_document import element_index, flag_codes, line_map, normalize, render_for_llm
from app.services.persistence import add_audit, sync_flags

_FEATURES = ["LAYOUT", "FORMS", "TABLES"]


def _money(settings: Settings, pages: int, input_tokens: int, output_tokens: int) -> Decimal:
    cost = Decimal(pages) * settings.textract_usd_per_page
    cost += (Decimal(input_tokens) / Decimal(1000)) * settings.bedrock_input_usd_per_1k_tokens
    cost += (Decimal(output_tokens) / Decimal(1000)) * settings.bedrock_output_usd_per_1k_tokens
    return cost.quantize(Decimal("0.000001"))


def _blank() -> list[MappedField]:
    return [MappedField(key, None, None, "not_found", [], None) for key in FIELD_KEYS]


class Pipeline:
    def __init__(
        self,
        *,
        storage: Any,
        textract: Any,
        model: Any,
        settings: Settings,
        clock: Clock,
        sleep: Any,
    ) -> None:
        self.storage = storage
        self.textract = textract
        self.model = model
        self.settings = settings
        self.clock = clock
        self.sleep = sleep

    def execute(self, session: Session, run_id: uuid.UUID) -> None:
        run = session.get(ProcessingRun, run_id)
        if run is None:
            return
        tokens = (
            request_id_var.set(str(run.request_id)),
            run_id_var.set(str(run.id)),
            referral_id_var.set(str(run.referral_id)),
        )
        try:
            self._execute(session, run_id)
        except Exception:
            session.rollback()
            log_event(
                logging.ERROR,
                "app.error.unhandled",
                exception_type="PipelineError",
                error_code="INTERNAL_ERROR",
            )
            self._unexpected(session, run_id)
        finally:
            request_id_var.reset(tokens[0])
            run_id_var.reset(tokens[1])
            referral_id_var.reset(tokens[2])

    def _execute(self, session: Session, run_id: uuid.UUID) -> None:
        if not self._begin(session, run_id):
            return
        run = session.get(ProcessingRun, run_id)
        document = self._document(session, run.referral_id) if run is not None else None
        if run is None or document is None:
            return
        document_id_var.set(str(document.id))
        deadline = self.clock.now() + timedelta(
            seconds=self.settings.processing_run_timeout_seconds
        )
        if self.clock.now() >= deadline:
            return
        ocr = self._ocr(session, run, document, deadline)
        if ocr is None:
            return
        session.refresh(run)
        if run.status == "failed":
            return
        if ocr.status != "succeeded" or not isinstance(ocr.normalized, dict):
            return
        if self.clock.now() >= deadline:
            return
        if "OCR_DOCUMENT_NO_TEXT" in (ocr.flag_codes or []):
            self._open_review(
                session,
                run.id,
                fields=_blank(),
                extraction=None,
                error_code=None,
                retryable=None,
                succeeded=True,
            )
            return
        existing = session.scalar(select(AiRun).where(AiRun.processing_run_id == run.id))
        if (
            existing is not None
            and existing.status == "succeeded"
            and isinstance(existing.validated_output, dict)
        ):
            self._open_review(
                session,
                run.id,
                fields=map_extraction(existing.validated_output, element_index(ocr.normalized)),
                extraction=existing.validated_output,
                error_code=None,
                retryable=None,
                succeeded=True,
            )
            return
        if existing is not None and existing.status == "failed":
            mapped, retryable = map_ai_error(existing.error_code or "AI_FAILED")
            self._open_review(
                session,
                run.id,
                fields=_blank(),
                extraction=None,
                error_code=mapped,
                retryable=retryable,
            )
            return
        if not self.settings.ai_enabled:
            self._open_review(
                session,
                run.id,
                fields=_blank(),
                extraction=None,
                error_code="AI_SKIPPED",
                retryable=True,
            )
            return
        result = self._extract(ocr, document)
        self._save_model(session, run, document, ocr, result)

    def _begin(self, session: Session, run_id: uuid.UUID) -> bool:
        run = session.scalar(
            select(ProcessingRun).where(ProcessingRun.id == run_id).with_for_update()
        )
        if run is None or run.status not in {"queued", "running"}:
            session.rollback()
            return False
        referral = session.scalar(
            select(Referral).where(Referral.id == run.referral_id).with_for_update()
        )
        if (
            referral is None
            or referral.current_run_id != run.id
            or referral.status not in {"uploaded", "processing"}
        ):
            session.rollback()
            return False
        now = self.clock.now()
        if run.status == "queued":
            run.status = "running"
            run.started_at = run.started_at or now
            log_event(logging.INFO, "run.started", attempt=run.attempt, trigger=run.trigger_kind)
        if referral.status == "uploaded":
            previous = referral.status
            referral.status = "processing"
            referral.version += 1
            referral.updated_at = now
            add_audit(
                session,
                action="processing_started",
                referral_id=referral.id,
                request_id=run.request_id,
                actor_type="system",
                document_id=self._document(session, referral.id).id,
                run_id=run.id,
                from_status=previous,
                to_status="processing",
            )
            log_event(
                logging.INFO, "run.status_changed", from_status=previous, to_status="processing"
            )
        run.updated_at = now
        session.commit()
        return True

    def _document(self, session: Session, referral_id: uuid.UUID) -> Document:
        document = session.scalar(select(Document).where(Document.referral_id == referral_id))
        if document is None:
            raise RuntimeError("document missing")
        return document

    def _ocr(
        self,
        session: Session,
        run: ProcessingRun,
        document: Document,
        deadline: datetime,
    ) -> OcrRun | None:
        ocr = session.get(OcrRun, run.ocr_run_id) if run.ocr_run_id else None
        if ocr is not None and ocr.status == "succeeded":
            return ocr
        now = self.clock.now()
        if (
            ocr is not None
            and ocr.textract_job_id
            and ocr.started_at is not None
            and now - ocr.started_at > JOB_TTL
            and ocr.status != "succeeded"
        ):
            self._fail(session, run.id, "OCR_JOB_EXPIRED", True, "ocr", "timeout")
            return None
        if ocr is None:
            ocr = OcrRun(
                id=uuid.uuid4(),
                document_id=document.id,
                referral_id=document.referral_id,
                created_by_run_id=run.id,
                attempt=run.attempt,
                config_version="1",
                feature_types=list(_FEATURES),
                client_request_token=client_request_token(document.id.hex, run.attempt),
                status="running",
                preflight_page_count=document.page_count,
                pages_billed_estimate=0,
                poll_count=0,
                flag_codes=[],
                started_at=now,
                updated_at=now,
            )
            session.add(ocr)
            session.flush()
            run.ocr_run_id = ocr.id
            run.updated_at = now
            session.commit()
        if ocr.textract_job_id is None:
            try:
                started = self.textract.start_job(
                    bucket=document.s3_bucket,
                    key=document.s3_key,
                    token=ocr.client_request_token,
                    job_tag=str(ocr.id),
                    features=list(_FEATURES),
                )
            except TextractFailure as exc:
                self._record_ocr_failure(session, run.id, ocr.id, exc.result)
                return None
            ocr.textract_job_id = started.job_id
            ocr.status = "running"
            ocr.updated_at = self.clock.now()
            run.textract_job_id = started.job_id
            run.aws_request_id = started.aws_request_id
            run.updated_at = ocr.updated_at
            session.commit()
            log_event(
                logging.INFO,
                "ocr.started",
                stage="ocr",
                textract_api="StartDocumentAnalysis",
                feature_types=list(_FEATURES),
                textract_job_id=started.job_id,
                aws_request_id=started.aws_request_id,
            )
        else:
            log_event(
                logging.INFO,
                "ocr.job_resumed",
                stage="ocr",
                textract_job_id=ocr.textract_job_id,
                attempt=run.attempt,
            )
        collected = self.textract.collect_job(
            ocr.textract_job_id,
            worker_deadline=deadline,
            now=self.clock.now,
            sleep=self.sleep,
        )
        ocr.poll_count = (ocr.poll_count or 0) + collected.poll_count
        ocr.updated_at = self.clock.now()
        if collected.kind == "yield_ocr":
            ocr.status = "timed_out"
            session.commit()
            log_event(
                logging.WARNING,
                "ocr.job_timed_out",
                stage="ocr",
                textract_job_id=ocr.textract_job_id,
                attempt=run.attempt,
            )
            return None
        if collected.kind == "yield_worker":
            session.commit()
            return None
        if collected.kind == "failed":
            self._record_ocr_failure(session, run.id, ocr.id, collected)
            return None
        if not self._store_ocr(session, run, document, ocr, collected):
            return None
        return ocr

    def _store_ocr(
        self,
        session: Session,
        run: ProcessingRun,
        document: Document,
        ocr: OcrRun,
        collected: OcrCollect,
    ) -> bool:
        raw = {
            "raw_schema": "textract-get-document-analysis-merged/1",
            "ocr_run_id": str(ocr.id),
            "document_id": str(document.id),
            "textract_job_id": ocr.textract_job_id,
            "feature_types": list(_FEATURES),
            "collected_at": self.clock.now().isoformat().replace("+00:00", "Z"),
            "job_status": collected.job_status,
            "document_metadata": {"Pages": collected.pages},
            "model_version": collected.model_version,
            "warnings": collected.warnings,
            "blocks": collected.blocks,
        }
        payload = gzip.compress(json.dumps(raw, separators=(",", ":"), default=str).encode())
        digest = hashlib.sha256(payload).digest()
        key = ocr_key(document.referral_id, ocr.id)
        try:
            head = write_once(
                self.storage,
                key=key,
                body=payload,
                content_type="application/gzip",
                sha256=digest,
                sha256_hex=digest.hex(),
            )
        except StorageError, ObjectMissing:
            self._fail(session, run.id, "STORAGE_FAILED", False, "ocr", "storage")
            return False
        normalized = normalize(
            {
                "blocks": collected.blocks,
                "document_metadata": {"Pages": collected.pages or 0},
                "model_version": collected.model_version,
                "warnings": collected.warnings,
                "job_status": collected.job_status or "SUCCEEDED",
            },
            ocr_run_id=str(ocr.id),
            document_id=str(document.id),
            feature_types=list(_FEATURES),
            textract_job_id=ocr.textract_job_id or "",
            region=self.settings.aws_region,
            preflight_page_count=document.page_count,
        )
        now = self.clock.now()
        ocr.status = "succeeded"
        ocr.raw_output_s3_key = key
        ocr.raw_output_sha256_hex = digest.hex()
        ocr.raw_output_size_bytes = len(payload)
        ocr.normalized = normalized
        ocr.normalizer_version = str(normalized["normalizer_version"])
        ocr.model_version = collected.model_version
        ocr.textract_page_count = normalized["page_count"]
        ocr.pages_billed_estimate = int(collected.pages or normalized["page_count"] or 0)
        ocr.flag_codes = flag_codes(normalized)
        mean = normalized.get("mean_word_confidence")
        ocr.mean_word_confidence = None if mean is None else Decimal(str(mean))
        ocr.completed_at = now
        ocr.updated_at = now
        run.textract_pages = ocr.pages_billed_estimate
        run.ocr_ms = _elapsed_ms(ocr.started_at, now)
        run.updated_at = now
        session.commit()
        log_event(
            logging.INFO,
            "ocr.completed",
            stage="ocr",
            pages=ocr.pages_billed_estimate or 0,
            block_count=len(collected.blocks),
            mean_confidence=float(mean) if isinstance(mean, int | float) else None,
            duration_ms=run.ocr_ms or 0,
            textract_job_id=ocr.textract_job_id,
        )
        _ = head
        return True

    def _extract(self, ocr: OcrRun, document: Document) -> AiRunResult:
        normalized = ocr.normalized if isinstance(ocr.normalized, dict) else {}
        transcript = render_for_llm(normalized)
        started = self.clock.now()
        result = self.model.extract(
            document_ref=str(document.id),
            page_count=int(normalized.get("page_count") or document.page_count or 1),
            transcript=transcript,
            lines=line_map(normalized),
        )
        result.latency_ms = (
            result.latency_ms
            if result.latency_ms is not None
            else _elapsed_ms(started, self.clock.now())
        )
        return result

    def _model_cost(self, result: AiRunResult) -> Decimal:
        if result.estimated_cost_usd is not None:
            return result.estimated_cost_usd
        return bedrock_token_cost(
            self.settings, result.input_tokens or 0, result.output_tokens or 0
        )

    def _save_model(
        self,
        session: Session,
        run: ProcessingRun,
        document: Document,
        ocr: OcrRun,
        result: AiRunResult,
    ) -> None:
        if result.status == "skipped":
            self._open_review(
                session,
                run.id,
                fields=_blank(),
                extraction=None,
                error_code="AI_SKIPPED",
                retryable=True,
            )
            return
        if result.status != "succeeded" or result.output is None:
            code, retryable = map_ai_error(result.error_code or "AI_FAILED")
            self._insert_ai(session, run, result, status="failed", error_code=result.error_code)
            log_event(
                logging.WARNING,
                "ai.failed",
                stage="ai",
                error_code=code,
                retryable=retryable,
                attempt=run.attempt,
                model_id=self.settings.bedrock_model_id,
                aws_request_id=result.aws_request_id,
            )
            self._open_review(
                session,
                run.id,
                fields=_blank(),
                extraction=None,
                error_code=code,
                retryable=retryable,
            )
            return
        normalized = ocr.normalized if isinstance(ocr.normalized, dict) else {}
        fields = map_extraction(result.output, element_index(normalized))
        self._insert_ai(session, run, result, status="succeeded", error_code=None)
        log_event(
            logging.INFO,
            "ai.completed",
            stage="ai",
            model_id=self.settings.bedrock_model_id,
            prompt_version="referral-extract-v1",
            input_tokens=result.input_tokens or 0,
            output_tokens=result.output_tokens or 0,
            latency_ms=result.latency_ms or 0,
            stop_reason=result.stop_reason or "end_turn",
            schema_valid=True,
            estimated_cost_usd=float(self._model_cost(result)),
            aws_request_id=result.aws_request_id,
        )
        self._open_review(
            session,
            run.id,
            fields=fields,
            extraction=result.output,
            error_code=None,
            retryable=None,
            succeeded=True,
        )
        _ = document

    def _insert_ai(
        self,
        session: Session,
        run: ProcessingRun,
        result: AiRunResult,
        *,
        status: str,
        error_code: str | None,
    ) -> None:
        if session.scalar(select(AiRun.id).where(AiRun.processing_run_id == run.id)) is not None:
            return
        attempts = result.attempts if result.attempts in {1, 2} else 1
        output = result.output if status == "succeeded" else None
        suggestion = output.get("specialty_suggestion") if isinstance(output, dict) else {}
        if not isinstance(suggestion, dict):
            suggestion = {}
        label = suggestion.get("label") if status == "succeeded" else None
        now = self.clock.now()
        session.add(
            AiRun(
                id=uuid.uuid4(),
                processing_run_id=run.id,
                referral_id=run.referral_id,
                status=status,
                model_id=result.model_id or self.settings.bedrock_model_id,
                prompt_version=result.prompt_version,
                schema_version=result.schema_version,
                invocation_count=attempts,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                latency_ms=result.latency_ms,
                estimated_cost_usd=self._model_cost(result),
                stop_reason=result.stop_reason,
                validated_output=output,
                raw_output_text=None if status == "succeeded" else result.raw_output_text,
                specialty_label=label if isinstance(label, str) else None,
                other_specialty_name=(suggestion.get("other_specialty_name") or None)
                if status == "succeeded"
                else None,
                relation_to_request=suggestion.get("relation_to_request")
                if status == "succeeded"
                else None,
                rationale=suggestion.get("rationale") if status == "succeeded" else None,
                candidates=(suggestion.get("candidates") or []) if status == "succeeded" else [],
                error_code=error_code,
                started_at=now,
                completed_at=now,
                updated_at=now,
            )
        )
        session.flush()

    def _open_review(
        self,
        session: Session,
        run_id: uuid.UUID,
        *,
        fields: list[MappedField],
        extraction: dict[str, Any] | None,
        error_code: str | None,
        retryable: bool | None,
        succeeded: bool = False,
    ) -> None:
        run = session.scalar(
            select(ProcessingRun).where(ProcessingRun.id == run_id).with_for_update()
        )
        if run is None:
            session.rollback()
            return
        referral = session.scalar(
            select(Referral).where(Referral.id == run.referral_id).with_for_update()
        )
        if referral is None or referral.current_run_id != run.id or referral.status != "processing":
            session.rollback()
            return
        document = self._document(session, referral.id)
        ocr = session.get(OcrRun, run.ocr_run_id) if run.ocr_run_id else None
        normalized = ocr.normalized if ocr is not None and isinstance(ocr.normalized, dict) else {}
        now = self.clock.now()
        existing = {
            row.field_key
            for row in session.scalars(
                select(ReferralField).where(ReferralField.processing_run_id == run.id)
            )
        }
        for item in fields:
            if item.key in existing:
                continue
            session.add(
                ReferralField(
                    id=uuid.uuid4(),
                    processing_run_id=run.id,
                    referral_id=referral.id,
                    field_key=item.key,
                    suggested_text=item.suggested_text,
                    suggested_json=item.suggested_json,
                    ai_status=item.ai_status,
                    evidence=item.evidence,
                    note=item.note,
                    confirmed_absent=False,
                    updated_at=now,
                )
            )
        session.flush()
        rows = list(
            session.scalars(select(ReferralField).where(ReferralField.processing_run_id == run.id))
        )
        known = set(element_index(normalized)) if normalized else None
        drafts = assemble_flags(
            [view_from_row(row) for row in rows],
            today=now.date(),
            duplicate=referral.possible_duplicate_of_referral_id is not None,
            ocr_flag_codes=list(ocr.flag_codes or []) if ocr is not None else [],
            known_ids=known,
            extraction=extraction,
            lines=line_map(normalized) if normalized else {},
            run_error_code=error_code,
        )
        sync_flags(session, referral_id=referral.id, run_id=run.id, desired=drafts, now=now)
        previous = referral.status
        referral.status = "needs_review"
        referral.version += 1
        referral.updated_at = now
        input_tokens = 0
        output_tokens = 0
        ai = session.scalar(select(AiRun).where(AiRun.processing_run_id == run.id))
        if ai is not None:
            input_tokens = ai.input_tokens or 0
            output_tokens = ai.output_tokens or 0
            run.ai_ms = ai.latency_ms
        pages = run.textract_pages or 0
        run.status = "succeeded" if succeeded else "failed"
        run.error_code = error_code
        run.retryable = retryable
        run.failure_stage = None if succeeded else "ai"
        run.failure_detail = None if succeeded else "ai"
        run.finished_at = now
        run.input_tokens = input_tokens or None
        run.output_tokens = output_tokens or None
        run.bedrock_model_id = self.settings.bedrock_model_id if ai is not None else None
        run.estimated_cost_usd = _money(self.settings, pages, input_tokens, output_tokens)
        run.pricing_rates_as_of = self.settings.pricing_rates_as_of
        run.updated_at = now
        add_audit(
            session,
            action="ready_for_review",
            referral_id=referral.id,
            request_id=run.request_id,
            actor_type="system",
            document_id=document.id,
            run_id=run.id,
            from_status=previous,
            to_status="needs_review",
        )
        session.commit()
        counts: dict[str, int] = {}
        for draft in drafts:
            counts[draft.code.lower()] = counts.get(draft.code.lower(), 0) + 1
        log_event(
            logging.INFO,
            "validation.completed",
            flag_count=len(drafts),
            flags_by_type=counts,
            flagged_fields=[draft.field_key for draft in drafts if draft.field_key],
            specialty_suggested=succeeded and extraction is not None,
            duration_ms=0,
        )
        log_event(logging.INFO, "results.persisted", to_status="needs_review", duration_ms=0)
        log_event(
            logging.INFO,
            "run.ready_for_review",
            stage="end_to_end",
            attempt=run.attempt,
            total_duration_ms=_elapsed_ms(run.started_at, now),
            textract_pages=pages,
            input_tokens_total=input_tokens,
            output_tokens_total=output_tokens,
            estimated_cost_usd=float(run.estimated_cost_usd or 0),
            flag_count=len(drafts),
        )

    def _record_ocr_failure(
        self,
        session: Session,
        run_id: uuid.UUID,
        ocr_id: uuid.UUID,
        collected: OcrCollect,
    ) -> None:
        ocr = session.get(OcrRun, ocr_id)
        if ocr is not None:
            ocr.status = "failed"
            ocr.error_category = collected.error_category
            ocr.error_code = collected.aws_error_code or collected.error_code
            ocr.status_message = collected.status_message
            ocr.completed_at = self.clock.now()
            ocr.updated_at = ocr.completed_at
            session.flush()
        code = "OCR_JOB_EXPIRED" if collected.error_code == "OCR_JOB_EXPIRED" else "OCR_FAILED"
        log_event(
            logging.ERROR,
            "ocr.failed",
            stage="ocr",
            error_code=code,
            aws_error_code=collected.aws_error_code,
            retryable=collected.retryable,
            attempt=1,
            aws_request_id=collected.aws_request_id,
        )
        self._fail(
            session,
            run_id,
            code,
            collected.retryable,
            "ocr",
            "ocr",
        )

    def _fail(
        self,
        session: Session,
        run_id: uuid.UUID,
        error_code: str,
        retryable: bool,
        stage: str,
        detail: str,
    ) -> None:
        run = session.scalar(
            select(ProcessingRun).where(ProcessingRun.id == run_id).with_for_update()
        )
        if run is None:
            session.rollback()
            return
        referral = session.scalar(
            select(Referral).where(Referral.id == run.referral_id).with_for_update()
        )
        if (
            referral is None
            or referral.current_run_id != run.id
            or referral.status
            not in {
                "uploaded",
                "processing",
            }
        ):
            session.rollback()
            return
        document = self._document(session, referral.id)
        now = self.clock.now()
        previous = referral.status
        referral.status = "failed"
        referral.version += 1
        referral.updated_at = now
        run.status = "failed"
        run.error_code = error_code
        run.retryable = retryable
        run.failure_stage = stage if stage in {"ocr", "ai", "other"} else "other"
        run.failure_detail = detail
        run.finished_at = now
        run.updated_at = now
        run.estimated_cost_usd = _money(self.settings, run.textract_pages or 0, 0, 0)
        run.pricing_rates_as_of = self.settings.pricing_rates_as_of
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
            stage=stage if stage in {"ocr", "ai", "other", "end_to_end"} else "other",
            failure_detail=detail,
            error_code=error_code,
            retryable=retryable,
            attempt=run.attempt,
            total_duration_ms=_elapsed_ms(run.started_at, now),
            estimated_cost_usd=float(run.estimated_cost_usd or 0),
        )

    def _unexpected(self, session: Session, run_id: uuid.UUID) -> None:
        run = session.get(ProcessingRun, run_id)
        if run is None or run.status not in {"queued", "running"}:
            return
        ocr = session.get(OcrRun, run.ocr_run_id) if run.ocr_run_id else None
        if ocr is not None and ocr.status == "succeeded":
            self._open_review(
                session,
                run_id,
                fields=_blank(),
                extraction=None,
                error_code="INTERRUPTED",
                retryable=True,
            )
            return
        self._fail(session, run_id, "INTERNAL_ERROR", True, "other", "unknown")


def _elapsed_ms(started: datetime | None, finished: datetime) -> int:
    if started is None:
        return 0
    return max(0, int((finished - started).total_seconds() * 1000))


def recover(session: Session, clock: Clock) -> list[uuid.UUID]:
    """Close runs that died during the model call. Return runs the runner should submit."""

    now = clock.now()
    submit: list[uuid.UUID] = []
    runs = list(
        session.scalars(
            select(ProcessingRun).where(ProcessingRun.status.in_(("queued", "running")))
        )
    )
    for run in runs:
        referral = session.get(Referral, run.referral_id)
        if referral is None or referral.current_run_id != run.id:
            continue
        if referral.status not in {"uploaded", "processing"}:
            continue
        ocr = session.get(OcrRun, run.ocr_run_id) if run.ocr_run_id else None
        if (
            ocr is not None
            and ocr.textract_job_id
            and ocr.started_at is not None
            and now - ocr.started_at > JOB_TTL
            and ocr.status != "succeeded"
        ):
            _expire(session, referral, run, now)
            continue
        if ocr is not None and ocr.status == "succeeded":
            ai = session.scalar(select(AiRun).where(AiRun.processing_run_id == run.id))
            if ai is None or ai.status in {"queued", "running"}:
                _interrupt(session, referral, run, now)
                continue
        submit.append(run.id)
    session.commit()
    return submit


def _expire(session: Session, referral: Referral, run: ProcessingRun, now: datetime) -> None:
    document = session.scalar(select(Document).where(Document.referral_id == referral.id))
    previous = referral.status
    referral.status = "failed"
    referral.version += 1
    referral.updated_at = now
    run.status = "failed"
    run.error_code = "OCR_JOB_EXPIRED"
    run.retryable = True
    run.failure_stage = "ocr"
    run.failure_detail = "timeout"
    run.finished_at = now
    run.updated_at = now
    if document is not None:
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


def _interrupt(session: Session, referral: Referral, run: ProcessingRun, now: datetime) -> None:
    document = session.scalar(select(Document).where(Document.referral_id == referral.id))
    existing = {
        row.field_key
        for row in session.scalars(
            select(ReferralField).where(ReferralField.processing_run_id == run.id)
        )
    }
    for key in FIELD_KEYS:
        if key in existing:
            continue
        session.add(
            ReferralField(
                id=uuid.uuid4(),
                processing_run_id=run.id,
                referral_id=referral.id,
                field_key=key,
                ai_status="not_found",
                evidence=[],
                confirmed_absent=False,
                updated_at=now,
            )
        )
    session.flush()
    rows = list(
        session.scalars(select(ReferralField).where(ReferralField.processing_run_id == run.id))
    )
    sync_flags(
        session,
        referral_id=referral.id,
        run_id=run.id,
        desired=assemble_flags([view_from_row(row) for row in rows], run_error_code="INTERRUPTED"),
        now=now,
    )
    previous = referral.status
    referral.status = "needs_review"
    referral.version += 1
    referral.updated_at = now
    run.status = "failed"
    run.error_code = "INTERRUPTED"
    run.retryable = True
    run.failure_stage = "ai"
    run.failure_detail = "ai"
    run.finished_at = now
    run.updated_at = now
    if document is not None:
        add_audit(
            session,
            action="ready_for_review",
            referral_id=referral.id,
            request_id=run.request_id,
            actor_type="system",
            document_id=document.id,
            run_id=run.id,
            from_status=previous,
            to_status="needs_review",
        )
