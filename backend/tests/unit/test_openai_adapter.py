"""Offline Responses API contract, failure, cost, and redaction checks."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import httpx2
import pytest
from openai import APITimeoutError, AuthenticationError, InternalServerError, RateLimitError
from tests.conftest import CANARY, build_settings
from tests.samples import sample_extraction

from app.adapters.llm_openai import OpenAILanguageModel, openai_token_cost
from app.ai.contracts import PROMPT_VERSION, SCHEMA_NAME, load_prompt, load_schema, map_ai_error
from app.core.logging import AllowListFormatter
from app.main import create_app


class _Client:
    def __init__(self, items: list[Any]) -> None:
        self.items = list(items)
        self.responses = self
        self.calls: list[dict[str, Any]] = []
        self.timeouts: list[float] = []

    def with_options(self, *, timeout: float) -> _Client:
        self.timeouts.append(timeout)
        return self

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        item = self.items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _payload(document_ref: str = "doc-1") -> str:
    body = sample_extraction()
    body["document_ref"] = document_ref
    return json.dumps(body)


def _response(
    text: str,
    *,
    status: str = "completed",
    input_tokens: int = 100,
    output_tokens: int = 200,
    request_id: str = "req_openai_1",
    refusal: bool = False,
) -> Any:
    content = [SimpleNamespace(type="refusal" if refusal else "output_text")]
    return SimpleNamespace(
        status=status,
        output_text=text,
        output=[SimpleNamespace(content=content)],
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
        _request_id=request_id,
    )


def _http_error(kind: type[AuthenticationError] | type[RateLimitError], code: str) -> Any:
    request = httpx2.Request("POST", "https://api.openai.com/v1/responses")
    response = httpx2.Response(429 if kind is RateLimitError else 401, request=request)
    return kind(CANARY, response=response, body={"code": code})


def test_request_contract_and_success() -> None:
    client = _Client([_response(_payload())])
    settings = build_settings()
    transcript = "<<<OCR_DOCUMENT ocr_run=x pages=1>>>\n[p1-l001] Jordan Sample"
    result = OpenAILanguageModel(client, settings).extract(
        document_ref="doc-1",
        page_count=1,
        transcript=transcript,
        lines={"p1-l001": "Jordan Sample"},
        deadline=datetime.now(UTC) + timedelta(seconds=200),
    )
    assert result.status == "succeeded"
    assert result.model_id == "gpt-6.1-terra"
    assert result.provider_request_id == "req_openai_1"
    assert result.estimated_cost_usd == Decimal("0.001100")
    assert result.attempts == 1
    assert result.output is not None
    assert len(client.calls) == 1
    sent = client.calls[0]
    assert sent == {
        "model": "gpt-6.1-terra",
        "instructions": load_prompt(),
        "input": f"document_ref: doc-1\npage_count: 1\n{transcript}",
        "text": {
            "format": {
                "type": "json_schema",
                "name": SCHEMA_NAME,
                "schema": load_schema(),
                "strict": True,
            }
        },
        "reasoning": {"effort": "medium"},
        "max_output_tokens": 6000,
        "store": False,
    }
    assert "file" not in sent and "tools" not in sent
    assert client.timeouts[0] <= 90


def test_repair_sums_usage_and_rejects_wrong_document_ref() -> None:
    client = _Client([_response("{"), _response(_payload(), request_id="req_openai_2")])
    result = OpenAILanguageModel(client, build_settings()).extract(
        document_ref="doc-1", page_count=1, transcript="short", lines={}
    )
    assert result.status == "succeeded"
    assert result.attempts == 2
    assert result.input_tokens == 200
    assert result.output_tokens == 400
    assert result.estimated_cost_usd == Decimal("0.002200")
    assert result.provider_request_id == "req_openai_2"
    assert [call["max_output_tokens"] for call in client.calls] == [6000, 12000]
    assert client.calls[0]["input"] == client.calls[1]["input"]

    wrong = _Client([_response(_payload("other")), _response(_payload("other"))])
    invalid = OpenAILanguageModel(wrong, build_settings()).extract(
        document_ref="doc-1", page_count=1, transcript="short", lines={}
    )
    assert invalid.error_code == "output_invalid"
    assert invalid.output is None
    assert invalid.attempts == 2


def test_refusal_incomplete_and_provider_errors() -> None:
    refused = OpenAILanguageModel(_Client([_response("", refusal=True)]), build_settings()).extract(
        document_ref="doc-1", page_count=1, transcript="short", lines={}
    )
    assert refused.error_code == "model_refusal"
    assert refused.attempts == 1
    assert map_ai_error(refused.error_code) == ("AI_FAILED", False)

    incomplete_client = _Client(
        [_response("", status="incomplete"), _response("", status="incomplete")]
    )
    incomplete = OpenAILanguageModel(incomplete_client, build_settings()).extract(
        document_ref="doc-1", page_count=1, transcript="short", lines={}
    )
    assert incomplete.error_code == "output_truncated"
    assert incomplete.attempts == 2

    for error, expected, retryable in (
        (_http_error(AuthenticationError, "invalid_api_key"), "openai_auth_failed", False),
        (_http_error(RateLimitError, "credit_balance_exhausted"), "openai_quota_exhausted", False),
        (_http_error(RateLimitError, "rate_limit_exceeded"), "openai_rate_limited", True),
        (
            InternalServerError(
                CANARY,
                response=httpx2.Response(
                    500,
                    request=httpx2.Request("POST", "https://api.openai.com/v1/responses"),
                ),
                body=None,
            ),
            "openai_unavailable",
            True,
        ),
        (
            APITimeoutError(httpx2.Request("POST", "https://api.openai.com/v1/responses")),
            "openai_timeout",
            True,
        ),
    ):
        client = _Client([error])
        result = OpenAILanguageModel(client, build_settings()).extract(
            document_ref="doc-1", page_count=1, transcript="short", lines={}
        )
        assert result.error_code == expected
        assert result.retryable is retryable
        assert result.attempts == 1
        assert CANARY not in str(result)


def test_disabled_limits_missing_key_and_safe_log() -> None:
    client = _Client([])
    disabled = OpenAILanguageModel(client, build_settings(ai_enabled=False)).extract(
        document_ref="doc-1", page_count=1, transcript=CANARY, lines={}
    )
    assert disabled.status == "skipped" and not client.calls
    oversized = OpenAILanguageModel(client, build_settings(openai_max_input_chars=1)).extract(
        document_ref="doc-1", page_count=1, transcript=CANARY, lines={}
    )
    assert oversized.error_code == "input_too_large" and not client.calls
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        create_app(build_settings(app_env="local", s3_bucket_name="synthetic-bucket"))

    formatter = AllowListFormatter(build_settings())
    record = logging.LogRecord("cortrucent", logging.WARNING, __file__, 1, CANARY, (), None)
    record._event = "ai.failed"  # type: ignore[attr-defined]
    record._fields = {  # type: ignore[attr-defined]
        "stage": "ai",
        "model_id": "gpt-6.1-terra",
        "provider_request_id": "req_openai_1",
        "error_code": "AI_FAILED",
        "response_body": CANARY,
    }
    line = formatter.format(record)
    assert CANARY not in line
    assert json.loads(line)["provider_request_id"] == "req_openai_1"
    assert openai_token_cost(build_settings(), 100, 200) == Decimal("0.001100")
    assert PROMPT_VERSION == "referral-extract-v1"
