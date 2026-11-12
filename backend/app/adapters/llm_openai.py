"""Active Responses API adapter: OCR transcript in, validated referral facts out."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    UnprocessableEntityError,
)
from pydantic import ValidationError

from app.ai.contracts import (
    PROMPT_VERSION,
    SCHEMA_NAME,
    AiRunResult,
    load_prompt,
    load_schema,
    user_message,
)
from app.core.config import Settings
from app.core.logging import log_event
from app.schemas.ai import ReferralExtractionV1
from app.services.grounding import ground

_QUOTA_CODES = {
    "credit_balance_exhausted",
    "organization_spend_limit_exceeded",
    "project_spend_limit_exceeded",
    "organization_usage_limit_exceeded",
    "insufficient_quota",
}


def openai_token_cost(settings: Settings, input_tokens: int, output_tokens: int) -> Decimal:
    cost = Decimal(input_tokens) * settings.openai_input_usd_per_1m_tokens / Decimal(1_000_000)
    cost += Decimal(output_tokens) * settings.openai_output_usd_per_1m_tokens / Decimal(1_000_000)
    return cost.quantize(Decimal("0.000001"))


@dataclass
class _Call:
    status: str
    text: str = ""
    input_tokens: int | None = None
    output_tokens: int | None = None
    request_id: str | None = None
    error_code: str | None = None


def _refused(response: Any) -> bool:
    for item in getattr(response, "output", ()) or ():
        for part in getattr(item, "content", ()) or ():
            if getattr(part, "type", None) == "refusal":
                return True
    return False


def _parse(text: str, document_ref: str) -> dict[str, Any] | None:
    try:
        loaded = json.loads(text)
        parsed = ReferralExtractionV1.model_validate(loaded)
    except json.JSONDecodeError, ValidationError, ValueError:
        return None
    if parsed.document_ref != document_ref:
        return None
    return parsed.model_dump()


def _provider_error(exc: Exception) -> str:
    if isinstance(exc, AuthenticationError):
        return "openai_auth_failed"
    if isinstance(exc, PermissionDeniedError):
        return "openai_access_denied"
    if isinstance(exc, NotFoundError):
        return "openai_model_not_found"
    if isinstance(exc, (BadRequestError, UnprocessableEntityError)):
        return "openai_request_invalid"
    if isinstance(exc, RateLimitError):
        return (
            "openai_quota_exhausted"
            if getattr(exc, "code", None) in _QUOTA_CODES
            else "openai_rate_limited"
        )
    if isinstance(exc, APITimeoutError):
        return "openai_timeout"
    if isinstance(exc, (APIConnectionError, InternalServerError)):
        return "openai_unavailable"
    return "openai_unavailable"


class OpenAILanguageModel:
    def __init__(self, client: Any, settings: Settings) -> None:
        self._client = client
        self._settings = settings
        self._prompt = load_prompt()
        self._schema = load_schema()

    def extract(
        self,
        *,
        document_ref: str,
        page_count: int,
        transcript: str,
        lines: dict[str, str] | None = None,
        deadline: datetime | None = None,
    ) -> AiRunResult:
        model_id = self._settings.openai_model_id
        if not self._settings.ai_enabled:
            return AiRunResult(status="skipped", error_code="ai_disabled", model_id=model_id)
        message = user_message(document_ref, page_count, transcript)
        if len(message) > self._settings.openai_max_input_chars:
            return AiRunResult(
                status="failed", error_code="input_too_large", model_id=model_id, retryable=False
            )
        log_event(
            logging.INFO,
            "ai.started",
            stage="ai",
            model_id=model_id,
            prompt_version=PROMPT_VERSION,
            input_chars=len(message),
        )
        started = time.monotonic()
        calls = [self._invoke(message, self._settings.openai_max_output_tokens, deadline)]
        first = calls[0]
        parsed = _parse(first.text, document_ref) if first.status == "completed" else None
        if (
            first.error_code is None
            and first.status != "refused"
            and (first.status == "incomplete" or (first.status == "completed" and parsed is None))
        ):
            if deadline is None or (deadline - datetime.now(UTC)).total_seconds() > 1:
                second = self._invoke(
                    message, self._settings.openai_retry_max_output_tokens, deadline
                )
                calls.append(second)
                parsed = _parse(second.text, document_ref) if second.status == "completed" else None
        last = calls[-1]
        input_tokens = (
            sum(call.input_tokens for call in calls if call.input_tokens is not None)
            if all(call.input_tokens is not None for call in calls)
            else None
        )
        output_tokens = (
            sum(call.output_tokens for call in calls if call.output_tokens is not None)
            if all(call.output_tokens is not None for call in calls)
            else None
        )
        cost = (
            openai_token_cost(self._settings, input_tokens, output_tokens)
            if input_tokens is not None and output_tokens is not None
            else None
        )
        code = last.error_code
        if code is None and last.status == "refused":
            code = "model_refusal"
        if code is None and last.status == "incomplete":
            code = "output_truncated"
        if code is None and (last.status != "completed" or parsed is None):
            code = "output_invalid"
        return AiRunResult(
            status="succeeded" if code is None else "failed",
            error_code=code,
            attempts=len(calls),
            stop_reason=last.status,
            latency_ms=int((time.monotonic() - started) * 1000),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost_usd=cost,
            output=parsed if code is None else None,
            warnings=ground(parsed, lines or {}) if code is None and parsed is not None else [],
            model_id=model_id,
            retryable=code
            in {
                "output_invalid",
                "output_truncated",
                "openai_rate_limited",
                "openai_timeout",
                "openai_unavailable",
            },
            provider_request_id=last.request_id,
        )

    def _invoke(self, message: str, max_output_tokens: int, deadline: datetime | None) -> _Call:
        timeout = float(self._settings.openai_timeout_seconds)
        if deadline is not None:
            timeout = min(timeout, (deadline - datetime.now(UTC)).total_seconds())
        if timeout <= 0:
            return _Call(status="failed", error_code="openai_timeout")
        try:
            response = self._client.with_options(timeout=timeout).responses.create(
                model=self._settings.openai_model_id,
                instructions=self._prompt,
                input=message,
                text={
                    "format": {
                        "type": "json_schema",
                        "name": SCHEMA_NAME,
                        "schema": self._schema,
                        "strict": True,
                    }
                },
                reasoning={"effort": "medium"},
                max_output_tokens=max_output_tokens,
                store=False,
            )
        except (APIStatusError, APIConnectionError) as exc:
            return _Call(
                status="failed",
                request_id=getattr(exc, "request_id", None),
                error_code=_provider_error(exc),
            )
        usage = response.usage
        status = "refused" if _refused(response) else response.status
        return _Call(
            status=status if isinstance(status, str) else "failed",
            text=response.output_text if isinstance(response.output_text, str) else "",
            input_tokens=getattr(usage, "input_tokens", None),
            output_tokens=getattr(usage, "output_tokens", None),
            request_id=getattr(response, "_request_id", None),
        )
