"""Allow-list JSON logs. Unknown fields and free-text messages are dropped."""

from __future__ import annotations

import json
import logging
import re
import sys
from collections.abc import Mapping
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any, TextIO

from app.core.config import Settings

SERVICE = "cortrucent-api"
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
origin_request_id_var: ContextVar[str | None] = ContextVar("origin_request_id", default=None)
run_id_var: ContextVar[str | None] = ContextVar("run_id", default=None)
referral_id_var: ContextVar[str | None] = ContextVar("referral_id", default=None)
document_id_var: ContextVar[str | None] = ContextVar("document_id", default=None)

_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_ROUTE = re.compile(r"^/[A-Za-z0-9_{}./-]*$")
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,80}$")
_LOGGER = re.compile(r"^[A-Za-z_][\w.]{0,80}$")
_SNAKE = re.compile(r"^[a-z][a-z0-9_]{0,40}$")
_FIELD = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
_FRAME = re.compile(r"^app/[\w./-]+:\d+ in [A-Za-z_][A-Za-z0-9_]*$")
_CONTENT_TYPE = re.compile(r"^[a-z]+/[a-z0-9.+-]{1,40}$")
_AWS_ID = re.compile(r"^[A-Za-z0-9+/_.:-]{1,128}$")
_MODEL_ID = re.compile(r"^[A-Za-z0-9._:-]{1,120}$")
_OBJECT_KEY = re.compile(
    r"^referrals/[0-9a-f-]{36}/documents/[0-9a-f-]{36}/"
    r"(source\.(pdf|png|jpg)|ocr/[0-9a-f-]{36}/textract-response\.json\.gz)$"
)
_VERSION = re.compile(r"^[A-Za-z0-9._+-]{1,80}$")

_METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})
_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})
_ENVS = frozenset({"local", "test", "deployed"})
_STAGES = frozenset({"ocr", "ai", "other", "end_to_end"})
_FAILURE_DETAILS = frozenset(
    {
        "upload",
        "storage",
        "ocr",
        "ai",
        "validation",
        "persistence",
        "timeout",
        "interrupted",
        "unknown",
        "expired",
    }
)
_RUN_CODES = frozenset(
    {
        "OCR_FAILED",
        "OCR_JOB_EXPIRED",
        "NO_TEXT_FOUND",
        "AI_FAILED",
        "AI_THROTTLED",
        "AI_OUTPUT_INVALID",
        "INTERRUPTED",
        "STORAGE_FAILED",
        "INTERNAL_ERROR",
    }
)
_REFERRAL_STATUSES = frozenset(
    {"uploaded", "processing", "needs_review", "failed", "approved", "rejected"}
)
_AUDIT_ACTIONS = frozenset(
    {
        "uploaded",
        "referral_viewed",
        "document_viewed",
        "field_corrected",
        "specialty_changed",
        "approved",
        "rejected",
        "reprocess_requested",
        "processing_started",
        "processing_failed",
        "ready_for_review",
    }
)
_AUTH_DENIED_REASONS = frozenset(
    {
        "missing_token",
        "malformed",
        "bad_signature",
        "wrong_issuer",
        "wrong_token_use",
        "wrong_client",
        "expired",
        "unknown_kid",
        "allow_list_empty",
    }
)
_AUTH_FORBIDDEN_REASONS = frozenset({"not_allow_listed", "allow_list_empty"})
_TRIGGERS = frozenset({"upload", "retry", "auto"})
_FEATURES = frozenset({"LAYOUT", "FORMS", "TABLES"})

# Event name to the fields that event may carry. The formatter adds the envelope.
_EVENTS: dict[str, dict[str, str]] = {
    "http.request.completed": {
        "method": "method",
        "route": "route",
        "status": "http_status",
        "duration_ms": "int",
        "actor_id": "uuid",
    },
    "auth.denied": {"method": "method", "route": "route", "reason": "auth_denied_reason"},
    "auth.forbidden": {
        "method": "method",
        "route": "route",
        "actor_id": "uuid",
        "reason": "auth_forbidden_reason",
    },
    "auth.jwks_unavailable": {"exception_type": "ident"},
    "referral.upload.received": {
        "content_type": "content_type",
        "size_bytes": "int",
        "actor_id": "uuid",
    },
    "referral.upload.rejected": {
        "reason": "snake",
        "content_type": "content_type",
        "size_bytes": "int",
    },
    "run.started": {"attempt": "int", "trigger": "trigger"},
    "document.stored": {
        "object_key": "object_key",
        "duration_ms": "int",
        "aws_request_id": "aws_id",
    },
    "ocr.started": {
        "stage": "stage",
        "textract_api": "ident",
        "feature_types": "features",
        "textract_job_id": "aws_id",
        "aws_request_id": "aws_id",
    },
    "ocr.completed": {
        "stage": "stage",
        "pages": "int",
        "block_count": "int",
        "mean_confidence": "confidence",
        "duration_ms": "int",
        "textract_job_id": "aws_id",
    },
    "ocr.failed": {
        "stage": "stage",
        "error_code": "run_code",
        "aws_error_code": "ident",
        "retryable": "bool",
        "attempt": "int",
        "aws_request_id": "aws_id",
    },
    "ocr.preflight_rejected": {"reason": "snake", "size_bytes": "int"},
    "ocr.job_timed_out": {
        "stage": "stage",
        "textract_job_id": "aws_id",
        "attempt": "int",
    },
    "ocr.job_resumed": {
        "stage": "stage",
        "textract_job_id": "aws_id",
        "attempt": "int",
    },
    "ai.started": {
        "stage": "stage",
        "model_id": "model_id",
        "prompt_version": "model_id",
        "input_chars": "int",
    },
    "ai.completed": {
        "stage": "stage",
        "model_id": "model_id",
        "prompt_version": "model_id",
        "input_tokens": "int",
        "output_tokens": "int",
        "latency_ms": "int",
        "stop_reason": "snake",
        "schema_valid": "bool",
        "estimated_cost_usd": "money",
        "aws_request_id": "aws_id",
    },
    "ai.failed": {
        "stage": "stage",
        "error_code": "run_code",
        "retryable": "bool",
        "attempt": "int",
        "model_id": "model_id",
        "aws_request_id": "aws_id",
    },
    "validation.completed": {
        "flag_count": "int",
        "flags_by_type": "flag_counts",
        "flagged_fields": "field_names",
        "specialty_suggested": "bool",
        "duration_ms": "int",
    },
    "results.persisted": {"to_status": "status", "duration_ms": "int"},
    "run.status_changed": {"from_status": "status", "to_status": "status"},
    "run.retry_scheduled": {
        "next_attempt": "int",
        "trigger": "trigger",
        "reason": "run_code",
        "delay_ms": "int",
    },
    "run.ready_for_review": {
        "stage": "stage",
        "attempt": "int",
        "total_duration_ms": "int",
        "textract_pages": "int",
        "input_tokens_total": "int",
        "output_tokens_total": "int",
        "estimated_cost_usd": "money",
        "flag_count": "int",
    },
    "run.failed": {
        "stage": "stage",
        "failure_detail": "failure_detail",
        "error_code": "run_code",
        "retryable": "bool",
        "attempt": "int",
        "total_duration_ms": "int",
        "estimated_cost_usd": "money",
    },
    "review.action": {
        "audit_event_id": "uuid",
        "action": "action",
        "actor_id": "uuid",
        "field_name": "field",
    },
    "app.error.unhandled": {
        "exception_type": "ident",
        "error_code": "run_code",
        "route": "route",
        "stack": "stack",
    },
    "library.log": {"logger": "logger"},
    "db.connected": {"ssl": "bool"},
}

_CONTEXT = (
    ("request_id", request_id_var),
    ("origin_request_id", origin_request_id_var),
    ("run_id", run_id_var),
    ("referral_id", referral_id_var),
    ("document_id", document_id_var),
)

_active_settings: Settings | None = None


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _accept(kind: str, value: object) -> object | None:
    if kind == "uuid" and isinstance(value, str) and _UUID.fullmatch(value):
        return value.lower()
    if kind == "method" and isinstance(value, str) and value in _METHODS:
        return value
    if kind == "route" and isinstance(value, str):
        if value == "unmatched" or _ROUTE.fullmatch(value):
            return value
    if kind == "http_status" and _is_int(value) and isinstance(value, int) and 100 <= value <= 599:
        return value
    if kind == "int" and _is_int(value) and isinstance(value, int) and 0 <= value <= 86_400_000:
        return value
    if kind == "bool" and isinstance(value, bool):
        return value
    if kind == "ident" and isinstance(value, str) and _IDENT.fullmatch(value):
        return value
    if kind == "logger" and isinstance(value, str) and _LOGGER.fullmatch(value):
        return value
    if kind == "snake" and isinstance(value, str) and _SNAKE.fullmatch(value):
        return value
    if kind == "field" and isinstance(value, str) and _FIELD.fullmatch(value):
        return value
    if kind == "auth_denied_reason" and value in _AUTH_DENIED_REASONS:
        return value
    if kind == "auth_forbidden_reason" and value in _AUTH_FORBIDDEN_REASONS:
        return value
    if kind == "trigger" and value in _TRIGGERS:
        return value
    if kind == "stage" and value in _STAGES:
        return value
    if kind == "failure_detail" and value in _FAILURE_DETAILS:
        return value
    if kind == "run_code" and value in _RUN_CODES:
        return value
    if kind == "status" and value in _REFERRAL_STATUSES:
        return value
    if kind == "action" and value in _AUDIT_ACTIONS:
        return value
    if kind == "content_type" and isinstance(value, str) and _CONTENT_TYPE.fullmatch(value):
        return value
    if kind == "aws_id" and isinstance(value, str) and _AWS_ID.fullmatch(value):
        return value
    if kind == "model_id" and isinstance(value, str) and _MODEL_ID.fullmatch(value):
        return value
    if kind == "object_key" and isinstance(value, str) and _OBJECT_KEY.fullmatch(value):
        return value
    if kind == "confidence" and _is_number(value) and isinstance(value, int | float):
        if 0 <= float(value) <= 100:
            return value
    if kind == "money" and _is_number(value) and isinstance(value, int | float):
        if 0 <= float(value) <= 100_000:
            return value
    if kind == "features" and isinstance(value, list):
        features = [item for item in value if isinstance(item, str) and item in _FEATURES]
        return features or None
    if kind == "field_names" and isinstance(value, list):
        names = [item for item in value if isinstance(item, str) and _FIELD.fullmatch(item)]
        return names or None
    if kind == "stack" and isinstance(value, list):
        frames = [item for item in value if isinstance(item, str) and _FRAME.fullmatch(item)]
        return frames[:8] or None
    if kind == "flag_counts" and isinstance(value, Mapping):
        counts: dict[str, int] = {}
        for key, item in value.items():
            if not isinstance(key, str) or _SNAKE.fullmatch(key) is None:
                continue
            if _is_int(item) and isinstance(item, int) and 0 <= item <= 10_000:
                counts[key] = item
        return counts or None
    return None


class _JsonStreamHandler(logging.StreamHandler[TextIO]):
    """Writes to the current ``sys.stdout`` so test capture and reload see the lines."""

    def emit(self, record: logging.LogRecord) -> None:
        self.stream = sys.stdout
        super().emit(record)


class AllowListFormatter(logging.Formatter):
    """One JSON object per line. The log message itself is never written."""

    def __init__(self, settings: Settings | None = None) -> None:
        super().__init__()
        self.settings = settings if settings is not None else _active_settings
        self.dropped_fields = 0

    def format(self, record: logging.LogRecord) -> str:
        event_name = getattr(record, "_event", None)
        fields = getattr(record, "_fields", None)
        if not isinstance(event_name, str) or event_name not in _EVENTS:
            event_name = "library.log"
            fields = {"logger": record.name}
        elif not isinstance(fields, dict):
            fields = {}
        level_name = record.levelname if record.levelname in _LEVELS else "ERROR"
        payload = self._payload(event_name, fields, level_name)
        try:
            return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        except TypeError, ValueError:
            return json.dumps(
                {
                    "ts": _timestamp(),
                    "level": "ERROR",
                    "service": SERVICE,
                    "env": self._env(),
                    "app_version": self._version(),
                    "event": "app.error.unhandled",
                    "error_code": "INTERNAL_ERROR",
                },
                separators=(",", ":"),
            )

    def _payload(
        self,
        event_name: str,
        fields: dict[str, object],
        level_name: str,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "ts": _timestamp(),
            "level": level_name,
            "service": SERVICE,
            "env": self._env(),
            "app_version": self._version(),
            "event": event_name,
        }
        for name, var in _CONTEXT:
            accepted = _accept("uuid", var.get())
            if isinstance(accepted, str):
                payload[name] = accepted
        schema = _EVENTS[event_name]
        for name, kind in schema.items():
            if name not in fields:
                continue
            accepted = _accept(kind, fields[name])
            if accepted is None:
                self.dropped_fields += 1
                continue
            payload[name] = accepted
        for name in fields:
            if name not in schema:
                self.dropped_fields += 1
        return payload

    def _env(self) -> str:
        if self.settings is None:
            return "local"
        value = str(self.settings.app_env)
        return value if value in _ENVS else "local"

    def _version(self) -> str:
        if self.settings is None or not _VERSION.fullmatch(self.settings.app_version):
            return "dev"
        return self.settings.app_version


def _timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def log_event(level: int, event: str, **fields: object) -> None:
    """Emit one allow-listed event. The message argument is the event name only."""

    logging.getLogger("cortrucent").log(
        level,
        "%s",
        event,
        extra={"_event": event, "_fields": fields},
    )


def configure_logging(settings: Settings) -> None:
    """Install the JSON formatter, quiet third-party loggers, and disable access logs."""

    global _active_settings
    _active_settings = settings
    level = getattr(logging, settings.log_level, logging.INFO)
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers = [
        handler for handler in root.handlers if not isinstance(handler, _JsonStreamHandler)
    ]
    handler = _JsonStreamHandler(sys.stdout)
    handler.setFormatter(AllowListFormatter(settings))
    root.addHandler(handler)
    app_logger = logging.getLogger("cortrucent")
    app_logger.setLevel(level)
    app_logger.propagate = True
    for name in (
        "botocore",
        "boto3",
        "urllib3",
        "httpx",
        "httpcore",
        "s3transfer",
        "sqlalchemy",
        "sqlalchemy.engine",
        "sqlalchemy.pool",
        "psycopg",
    ):
        logging.getLogger(name).setLevel(logging.WARNING)
    access = logging.getLogger("uvicorn.access")
    access.disabled = True
    access.propagate = False
    for name in ("uvicorn", "uvicorn.error"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
        logger.setLevel(level)


def route_template(scope: Mapping[str, Any]) -> str:
    """Return the matched route template, never the raw path or query string."""

    route = scope.get("route")
    path = getattr(route, "path", None)
    if isinstance(path, str) and _ROUTE.fullmatch(path):
        return path
    return "unmatched"
