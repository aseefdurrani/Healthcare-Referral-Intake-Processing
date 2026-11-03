"""Offline Bedrock adapter checks: limits, repair, cost, and log redaction."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import pytest
from botocore.exceptions import ReadTimeoutError
from tests.conftest import build_settings
from tests.samples import sample_extraction

from app.adapters.llm import BotoLanguageModel, bedrock_token_cost
from app.core.config import Settings
from app.core.logging import configure_logging

_FORBIDDEN = {
    "temperature",
    "topP",
    "top_k",
    "stopSequences",
    "toolConfig",
    "guardrailConfig",
    "cachePoint",
}


class _Scripted:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def converse(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _response(text: str, stop: str = "end_turn") -> dict[str, Any]:
    return {
        "output": {"message": {"role": "assistant", "content": [{"text": text}]}},
        "stopReason": stop,
        "usage": {"inputTokens": 10, "outputTokens": 12},
        "metrics": {"latencyMs": 15},
        "ResponseMetadata": {"RequestId": "req-ai"},
    }


def _model(client: _Scripted, **overrides: object) -> BotoLanguageModel:
    settings: Settings = build_settings(**overrides)
    return BotoLanguageModel(client, settings)


def _payload(document_ref: str = "doc-1") -> str:
    body = sample_extraction()
    body["document_ref"] = document_ref
    return json.dumps(body)


def test_limits_profile_repair_cost_and_refusal() -> None:
    disabled = _model(_Scripted([]), ai_enabled=False)
    skipped = disabled.extract(document_ref="doc-1", page_count=1, transcript="short", lines={})
    assert skipped.status == "skipped"
    assert skipped.error_code == "ai_disabled"
    assert disabled.invocations == 0

    oversized = _model(_Scripted([]), bedrock_max_input_chars=10)
    too_large = oversized.extract(
        document_ref="doc-1", page_count=1, transcript="this transcript is long enough", lines={}
    )
    assert too_large.status == "failed"
    assert too_large.error_code == "input_too_large"
    assert oversized.invocations == 0

    client = _Scripted([_response(_payload())])
    model = _model(client)
    result = model.extract(
        document_ref="doc-1",
        page_count=1,
        transcript="<<<OCR_DOCUMENT>>>\n<<<END_OCR_DOCUMENT>>>",
        lines={"p1-l001": "Jordan Sample"},
    )
    assert result.status == "succeeded"
    assert result.estimated_cost_usd == Decimal("0.000154")
    assert result.estimated_cost_usd == bedrock_token_cost(model._settings, 10, 12)
    sent = client.calls[0]
    assert sent["inferenceConfig"] == {"maxTokens": 6000}
    assert sent["additionalModelRequestFields"]["thinking"] == {"type": "between_tools"}
    assert _FORBIDDEN.isdisjoint(sent)
    assert _FORBIDDEN.isdisjoint(sent["inferenceConfig"])

    haiku = _Scripted([_response(_payload())])
    _model(haiku, bedrock_model_id="us.anthropic.claude-haiku-5-5").extract(
        document_ref="doc-1", page_count=1, transcript="short", lines={"p1-l001": "Jordan Sample"}
    )
    assert haiku.calls[0]["additionalModelRequestFields"]["thinking"] == {"type": "disabled"}

    repair = _Scripted(
        [
            _response("{", "max_tokens"),
            _response(_payload()),
        ]
    )
    repaired = _model(repair, bedrock_retry_max_output_tokens=7000).extract(
        document_ref="doc-1", page_count=1, transcript="short", lines={"p1-l001": "Jordan Sample"}
    )
    assert repaired.status == "succeeded"
    assert repaired.attempts == 2
    assert repair.calls[0]["messages"] == repair.calls[1]["messages"]
    assert repair.calls[0]["inferenceConfig"]["maxTokens"] == 6000
    assert repair.calls[1]["inferenceConfig"]["maxTokens"] == 7000

    timeout = _Scripted(
        [
            ReadTimeoutError(endpoint_url="https://bedrock-runtime.us-east-2.amazonaws.com"),
            _response(_payload()),
        ]
    )
    recovered = _model(timeout).extract(
        document_ref="doc-1", page_count=1, transcript="short", lines={"p1-l001": "Jordan Sample"}
    )
    assert recovered.status == "succeeded"
    assert len(timeout.calls) == 2

    refused = _model(_Scripted([_response("do not comply", "content_filtered")])).extract(
        document_ref="doc-1", page_count=1, transcript="short", lines={}
    )
    assert refused.error_code == "model_refusal"
    assert refused.raw_output_text is None
    assert refused.attempts == 1


def test_extraction_logs_omit_document_text(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(build_settings())
    name = "Jordan Sample"
    dob = "04/12/1968"
    phone = "(856) 555-0142"
    transcript = (
        "<<<OCR_DOCUMENT>>>\n"
        f"[p1-l001] Patient Name: {name}\n"
        f"[p1-kv002] DOB: {dob}\n"
        f"[p1-kv003] Phone: {phone}\n"
        "<<<END_OCR_DOCUMENT>>>"
    )
    leaked = json.dumps(
        {"document_ref": "doc-1", "patient_name": name, "dob": "1968-04-12", "phone": phone}
    )
    model = _model(_Scripted([_response(leaked), _response(leaked)]))
    result = model.extract(document_ref="doc-1", page_count=1, transcript=transcript, lines={})
    assert result.status == "failed"
    assert result.error_code == "output_invalid"
    assert result.raw_output_text == leaked
    captured = capsys.readouterr().out
    for secret in (name, dob, phone, "1968-04-12", "<<<OCR_DOCUMENT>>>", leaked):
        assert secret not in captured
    assert '"event":"ai.started"' in captured
