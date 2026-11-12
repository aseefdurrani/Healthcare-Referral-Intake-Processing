"""Provider-neutral extraction contract and the single prompt/schema source."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

PROMPT_VERSION = "referral-extract-v1"
SCHEMA_VERSION = "referral_extraction_v1"
SCHEMA_NAME = "referral_extraction_v1"
SCHEMA_DESCRIPTION = (
    "Structured referral facts with evidence and a non-binding specialty suggestion"
)
_ROOT = Path(__file__).resolve().parent
PROMPT_PATH = _ROOT / "prompts" / "referral_extract_v1.txt"
SCHEMA_PATH = _ROOT / "schema" / "referral_extraction_v1.json"


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
    estimated_cost_usd: Decimal | None = None
    output: dict[str, Any] | None = None
    warnings: list[dict[str, str]] = field(default_factory=list)
    raw_output_text: str | None = None
    model_id: str = ""
    prompt_version: str = PROMPT_VERSION
    schema_version: str = SCHEMA_VERSION
    retryable: bool = False
    aws_request_id: str | None = None  # Inactive Bedrock adapter only.
    provider_request_id: str | None = None


def map_ai_error(code: str) -> tuple[str, bool]:
    """Map provider failures to the existing review and manual-retry contract."""

    table = {
        "output_invalid": ("AI_OUTPUT_INVALID", True),
        "output_truncated": ("AI_OUTPUT_INVALID", True),
        "model_refusal": ("AI_FAILED", False),
        "input_too_large": ("AI_FAILED", False),
        "ai_disabled": ("AI_SKIPPED", True),
        "openai_rate_limited": ("AI_THROTTLED", True),
        "openai_timeout": ("AI_THROTTLED", True),
        "openai_unavailable": ("AI_FAILED", True),
        "openai_auth_failed": ("AI_FAILED", False),
        "openai_access_denied": ("AI_FAILED", False),
        "openai_quota_exhausted": ("AI_FAILED", False),
        "openai_request_invalid": ("AI_FAILED", False),
        "openai_model_not_found": ("AI_FAILED", False),
        "bedrock_request_invalid": ("AI_OUTPUT_INVALID", True),
        "bedrock_throttled": ("AI_THROTTLED", True),
        "bedrock_timeout": ("AI_THROTTLED", True),
        "bedrock_unavailable": ("AI_FAILED", True),
        "bedrock_access_denied": ("AI_FAILED", False),
        "bedrock_model_not_found": ("AI_FAILED", False),
    }
    return table.get(code, ("AI_FAILED", True))
