"""Bedrock Converse adapter. One repair call, then a recorded failure."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from botocore.exceptions import (  # type: ignore[import-untyped]
    ClientError,
    ConnectTimeoutError,
    EndpointConnectionError,
    ReadTimeoutError,
)
from pydantic import ValidationError

from app.core.config import Settings
from app.schemas.ai import ReferralExtractionV1
from app.services.grounding import ground
from app.services.ocr_document import line_map

PROMPT_VERSION = "referral-extract-v1"
SCHEMA_VERSION = "referral_extraction_v1"
SCHEMA_NAME = "referral_extraction_v1"
SCHEMA_DESCRIPTION = (
    "Structured referral facts with evidence and a non-binding specialty suggestion"
)
MAX_INPUT_CHARS = 120_000
RETRY_MAX_TOKENS = 12_000
_ROOT = Path(__file__).resolve().parents[1]
PROMPT_PATH = _ROOT / "ai" / "prompts" / "referral_extract_v1.txt"
SCHEMA_PATH = _ROOT / "ai" / "schema" / "referral_extraction_v1.json"

MODEL_PROFILES: dict[str, dict[str, object]] = {
    "us.anthropic.claude-sonnet-5-5": {
        "thinking": {"type": "between_tools"},
        "output_config": {"effort": "medium"},
    },
    "us.anthropic.claude-haiku-5-5": {
        "thinking": {"type": "disabled"},
        "output_config": {"effort": "medium"},
    },
}

_RETRYABLE_STOPS = {"max_tokens", "malformed_model_output"}
_NO_RETRY = {
    "AccessDeniedException": "bedrock_access_denied",
    "ValidationException": "bedrock_request_invalid",
    "ResourceNotFoundException": "bedrock_model_not_found",
}
_THROTTLE = {"ThrottlingException": "bedrock_throttled"}
_UNAVAILABLE = {
    "ServiceUnavailableException": "bedrock_unavailable",
    "InternalServerException": "bedrock_unavailable",
    "ModelTimeoutException": "bedrock_timeout",
}


def load_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8").rstrip("\n")


def load_schema() -> dict[str, Any]:
    loaded = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError("schema")
    return loaded


def user_message(document_ref: str, page_count: int, transcript: str) -> str:
    return f"document_ref: {document_ref}\npage_count: {page_count}\n{transcript}"


@dataclass
class AiRunResult:
    status: str
    error_code: str | None = None
    attempts: int = 0
    stop_reason: str | None = None
    latency_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    output: dict[str, Any] | None = None
    warnings: list[dict[str, str]] = field(default_factory=list)
    raw_output_text: str | None = None
    model_id: str = ""
    prompt_version: str = PROMPT_VERSION
    schema_version: str = SCHEMA_VERSION
    retryable: bool = False
    aws_request_id: str | None = None


@dataclass
class _Call:
    stop_reason: str | None
    text: str
    input_tokens: int
    output_tokens: int
    latency_ms: int | None
    aws_request_id: str | None
    error_code: str | None = None
    retryable: bool = False


def _request_id(response: dict[str, Any]) -> str | None:
    value = response.get("ResponseMetadata", {}).get("RequestId")
    return value if isinstance(value, str) else None


class BotoLanguageModel:
    def __init__(
        self, client: Any, settings: Settings, *, line_lookup: dict[str, str] | None = None
    ) -> None:
        self._client = client
        self._settings = settings
        self._prompt = load_prompt()
        self._schema = load_schema()
        self.invocations = 0
        self.line_lookup = line_lookup or {}

    def extract(
        self,
        *,
        document_ref: str,
        page_count: int,
        transcript: str,
        lines: dict[str, str] | None = None,
    ) -> AiRunResult:
        if not self._settings.ai_enabled:
            return AiRunResult(
                status="skipped", error_code="ai_disabled", model_id=self._settings.bedrock_model_id
            )
        if len(transcript) > MAX_INPUT_CHARS:
            return AiRunResult(
                status="failed",
                error_code="input_too_large",
                model_id=self._settings.bedrock_model_id,
                retryable=False,
            )
        message = user_message(document_ref, page_count, transcript)
        lookup = lines or self.line_lookup
        first = self._invoke(message, self._settings.bedrock_max_output_tokens)
        if self._needs_repair(first, document_ref) and self.invocations < 2:
            repair = message + "\n\nThe previous response was invalid. Return only the JSON object."
            second = self._invoke(repair, RETRY_MAX_TOKENS)
            return self._from_call(second, document_ref, lookup, attempts=self.invocations)
        return self._from_call(first, document_ref, lookup, attempts=self.invocations)

    def _needs_repair(self, call: _Call, document_ref: str) -> bool:
        if call.error_code == "bedrock_timeout":
            return True
        if call.error_code is not None:
            return False
        if call.stop_reason not in {None, "end_turn"} | _RETRYABLE_STOPS:
            return False
        if call.stop_reason in _RETRYABLE_STOPS:
            return True
        return self._parse(call.text, document_ref) is None

    def _invoke(self, text: str, max_tokens: int) -> _Call:
        self.invocations += 1
        kwargs: dict[str, object] = {
            "modelId": self._settings.bedrock_model_id,
            "system": [{"text": self._prompt}],
            "messages": [{"role": "user", "content": [{"text": text}]}],
            "inferenceConfig": {"maxTokens": max_tokens},
            "outputConfig": {
                "textFormat": {
                    "type": "json_schema",
                    "structure": {
                        "jsonSchema": {
                            "schema": json.dumps(self._schema),
                            "name": SCHEMA_NAME,
                            "description": SCHEMA_DESCRIPTION,
                        }
                    },
                }
            },
        }
        profile = MODEL_PROFILES.get(self._settings.bedrock_model_id)
        if profile is not None:
            kwargs["additionalModelRequestFields"] = profile
        try:
            response = self._client.converse(**kwargs)
        except (ClientError, ConnectTimeoutError, ReadTimeoutError, EndpointConnectionError) as exc:
            return self._from_exception(exc)
        parts = response.get("output", {}).get("message", {}).get("content", [])
        text_out = "".join(
            part.get("text", "")
            for part in parts
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        )
        usage = response.get("usage") or {}
        return _Call(
            stop_reason=response.get("stopReason")
            if isinstance(response.get("stopReason"), str)
            else None,
            text=text_out,
            input_tokens=int(usage.get("inputTokens") or 0),
            output_tokens=int(usage.get("outputTokens") or 0),
            latency_ms=response.get("metrics", {}).get("latencyMs"),
            aws_request_id=_request_id(response),
        )

    def _from_exception(self, exc: Exception) -> _Call:
        name = (
            exc.response.get("Error", {}).get("Code", "")
            if isinstance(exc, ClientError)
            else type(exc).__name__
        )
        if not isinstance(name, str):
            name = "ClientError"
        if name in _NO_RETRY:
            code = _NO_RETRY[name]
            retryable = False
        elif name in _THROTTLE or name in {
            "ConnectTimeoutError",
            "ReadTimeoutError",
            "EndpointConnectionError",
        }:
            code = (
                "bedrock_timeout"
                if "Timeout" in name or name.endswith("TimeoutError")
                else "bedrock_throttled"
            )
            if name in {"ConnectTimeoutError", "ReadTimeoutError", "EndpointConnectionError"}:
                code = "bedrock_timeout"
            retryable = True
        elif name in _UNAVAILABLE:
            code = _UNAVAILABLE[name]
            retryable = True
        else:
            code = "bedrock_unavailable"
            retryable = True
        return _Call(
            stop_reason=None,
            text="",
            input_tokens=0,
            output_tokens=0,
            latency_ms=None,
            aws_request_id=None,
            error_code=code,
            retryable=retryable,
        )

    def _parse(self, text: str, document_ref: str) -> dict[str, Any] | None:
        try:
            loaded = json.loads(text)
            model = ReferralExtractionV1.model_validate(loaded)
        except json.JSONDecodeError, ValidationError, ValueError:
            return None
        if model.document_ref != document_ref:
            return None
        return model.model_dump()

    def _success(
        self,
        call: _Call,
        output: dict[str, Any],
        lines: dict[str, str],
        *,
        attempts: int,
    ) -> AiRunResult:
        if call.stop_reason not in {None, "end_turn"} and call.stop_reason not in _RETRYABLE_STOPS:
            return AiRunResult(
                status="failed",
                error_code="model_refusal",
                attempts=attempts,
                stop_reason=call.stop_reason,
                input_tokens=call.input_tokens,
                output_tokens=call.output_tokens,
                latency_ms=call.latency_ms,
                model_id=self._settings.bedrock_model_id,
                retryable=True,
                aws_request_id=call.aws_request_id,
            )
        return AiRunResult(
            status="succeeded",
            attempts=attempts,
            stop_reason=call.stop_reason,
            input_tokens=call.input_tokens,
            output_tokens=call.output_tokens,
            latency_ms=call.latency_ms,
            output=output,
            warnings=ground(output, lines),
            model_id=self._settings.bedrock_model_id,
            aws_request_id=call.aws_request_id,
        )

    def _invalid(self, call: _Call, *, attempts: int) -> AiRunResult:
        if call.stop_reason not in {None, "end_turn"} | _RETRYABLE_STOPS:
            code = "model_refusal"
        elif call.stop_reason == "max_tokens":
            code = "output_truncated"
        else:
            code = "output_invalid"
        return AiRunResult(
            status="failed",
            error_code=code,
            attempts=attempts,
            stop_reason=call.stop_reason,
            input_tokens=call.input_tokens,
            output_tokens=call.output_tokens,
            latency_ms=call.latency_ms,
            raw_output_text=call.text[:500_000] or None,
            model_id=self._settings.bedrock_model_id,
            retryable=True,
            aws_request_id=call.aws_request_id,
        )

    def _from_call(
        self,
        call: _Call,
        document_ref: str,
        lines: dict[str, str],
        *,
        attempts: int,
    ) -> AiRunResult:
        if call.error_code is not None:
            return AiRunResult(
                status="failed",
                error_code=call.error_code,
                attempts=attempts,
                input_tokens=call.input_tokens,
                output_tokens=call.output_tokens,
                latency_ms=call.latency_ms,
                model_id=self._settings.bedrock_model_id,
                retryable=call.retryable,
                aws_request_id=call.aws_request_id,
            )
        if call.stop_reason not in {None, "end_turn"} | _RETRYABLE_STOPS:
            return self._invalid(call, attempts=attempts)
        parsed = self._parse(call.text, document_ref)
        if parsed is None:
            return self._invalid(call, attempts=attempts)
        return self._success(call, parsed, lines, attempts=attempts)


def map_ai_error(code: str) -> tuple[str, bool]:
    """Map an adapter code onto a processing-run code and whether staff may retry."""

    table = {
        "output_invalid": ("AI_OUTPUT_INVALID", True),
        "output_truncated": ("AI_OUTPUT_INVALID", True),
        "bedrock_request_invalid": ("AI_OUTPUT_INVALID", True),
        "bedrock_throttled": ("AI_THROTTLED", True),
        "bedrock_timeout": ("AI_THROTTLED", True),
        "bedrock_unavailable": ("AI_FAILED", True),
        "model_refusal": ("AI_FAILED", True),
        "bedrock_access_denied": ("AI_FAILED", False),
        "bedrock_model_not_found": ("AI_FAILED", False),
        "input_too_large": ("AI_FAILED", False),
        "ai_disabled": ("AI_SKIPPED", True),
    }
    return table.get(code, ("AI_FAILED", True))


def transcript_lines(document: dict[str, Any]) -> dict[str, str]:
    return line_map(document)
