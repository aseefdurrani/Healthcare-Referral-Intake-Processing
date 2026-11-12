"""botocore Stubber coverage for storage, Textract, and Bedrock."""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import UTC, datetime, timedelta

import boto3
from botocore.config import Config
from botocore.stub import Stubber
from tests.conftest import build_settings
from tests.samples import sample_extraction

from app.adapters.aws import _config
from app.adapters.llm import BotoLanguageModel
from app.adapters.ocr import BotoTextract
from app.adapters.storage import S3Storage
from app.core.config import Settings
from app.services.objects import write_once

BUCKET = "referrals-test-bucket"
KEY = "referrals/3f6c2a9e-8b1d-4c7a-9e2f-5d4b1a0c7e93/source.pdf"


def _settings() -> Settings:
    return build_settings(s3_bucket_name=BUCKET)


def test_aws_clients_use_standard_retries() -> None:
    config = _config(_settings(), 30)
    assert config.retries["mode"] == "standard"
    assert config.retries["total_max_attempts"] == 3
    assert config.connect_timeout == 5


def test_put_uses_if_none_match_and_checksum_without_sse_headers() -> None:
    client = boto3.client(
        "s3",
        region_name="us-east-2",
        config=Config(retries={"mode": "standard", "total_max_attempts": 1}),
    )
    body = b"%PDF-1.7"
    digest = hashlib.sha256(body).digest()
    stubber = Stubber(client)
    stubber.add_response(
        "put_object",
        {"ETag": '"abc"', "ResponseMetadata": {"RequestId": "req-1"}},
        {
            "Bucket": BUCKET,
            "Key": KEY,
            "Body": body,
            "ContentType": "application/pdf",
            "ChecksumSHA256": base64.b64encode(digest).decode("ascii"),
            "IfNoneMatch": "*",
        },
    )
    stubber.add_response(
        "head_object",
        {
            "ETag": '"abc"',
            "ServerSideEncryption": "aws:kms",
            "BucketKeyEnabled": True,
            "ChecksumSHA256": base64.b64encode(digest).decode("ascii"),
            "ContentLength": len(body),
            "ResponseMetadata": {"RequestId": "req-2"},
        },
        {"Bucket": BUCKET, "Key": KEY, "ChecksumMode": "ENABLED"},
    )
    stubber.activate()
    head = write_once(
        S3Storage(client, BUCKET),
        key=KEY,
        body=body,
        content_type="application/pdf",
        sha256=digest,
        sha256_hex=digest.hex(),
    )
    stubber.assert_no_pending_responses()
    assert head.sse == "aws:kms"
    assert head.bucket_key is True


def test_textract_paginates_and_reuses_the_client_token() -> None:
    client = boto3.client(
        "textract",
        region_name="us-east-2",
        config=Config(retries={"mode": "standard", "total_max_attempts": 1}),
    )
    token = "ocr-" + ("ab" * 16) + "-c1-a1"
    stubber = Stubber(client)
    expected = {
        "DocumentLocation": {"S3Object": {"Bucket": BUCKET, "Name": KEY}},
        "FeatureTypes": ["LAYOUT", "FORMS", "TABLES"],
        "ClientRequestToken": token,
        "JobTag": "6b2e8f14-9a3c-4d7e-8f05-1c4a9b6d3e27",
    }
    stubber.add_response("start_document_analysis", {"JobId": "job-1"}, expected)
    stubber.add_response("start_document_analysis", {"JobId": "job-1"}, expected)
    stubber.add_response(
        "get_document_analysis",
        {
            "JobStatus": "SUCCEEDED",
            "NextToken": "page-2",
            "Blocks": [{"Id": "a", "BlockType": "LINE", "Text": "one"}],
            "DocumentMetadata": {"Pages": 2},
            "AnalyzeDocumentModelVersion": "1",
        },
        {"JobId": "job-1", "MaxResults": 1000},
    )
    stubber.add_response(
        "get_document_analysis",
        {
            "JobStatus": "SUCCEEDED",
            "Blocks": [{"Id": "b", "BlockType": "LINE", "Text": "two"}],
            "DocumentMetadata": {"Pages": 2},
        },
        {"JobId": "job-1", "MaxResults": 1000, "NextToken": "page-2"},
    )
    stubber.activate()
    adapter = BotoTextract(client, _settings())
    first = adapter.start_job(
        bucket=BUCKET,
        key=KEY,
        token=token,
        job_tag=str(expected["JobTag"]),
        features=["LAYOUT", "FORMS", "TABLES"],
    )
    second = adapter.start_job(
        bucket=BUCKET,
        key=KEY,
        token=token,
        job_tag=str(expected["JobTag"]),
        features=["LAYOUT", "FORMS", "TABLES"],
    )
    assert first.job_id == second.job_id == "job-1"
    collected = adapter.collect_job(
        "job-1",
        worker_deadline=datetime.now(UTC) + timedelta(seconds=30),
        now=lambda: datetime.now(UTC),
        sleep=lambda _seconds: None,
    )
    assert [block["Text"] for block in collected.blocks] == ["one", "two"]
    stubber.assert_no_pending_responses()


def test_textract_resume_polls_without_starting() -> None:
    client = boto3.client(
        "textract",
        region_name="us-east-2",
        config=Config(retries={"mode": "standard", "total_max_attempts": 1}),
    )
    stubber = Stubber(client)
    stubber.add_response(
        "get_document_analysis",
        {
            "JobStatus": "SUCCEEDED",
            "Blocks": [{"Id": "only", "BlockType": "WORD"}],
            "DocumentMetadata": {"Pages": 1},
        },
        {"JobId": "existing-job", "MaxResults": 1000},
    )
    stubber.activate()
    collected = BotoTextract(client, _settings()).collect_job(
        "existing-job",
        worker_deadline=datetime.now(UTC) + timedelta(seconds=30),
        now=lambda: datetime.now(UTC),
        sleep=lambda _seconds: None,
    )
    assert collected.kind == "succeeded"
    assert collected.blocks[0]["Id"] == "only"
    stubber.assert_no_pending_responses()


def _bedrock(retries: int = 1) -> tuple[object, Stubber, BotoLanguageModel]:
    client = boto3.client(
        "bedrock-runtime",
        region_name="us-east-2",
        config=Config(retries={"mode": "standard", "total_max_attempts": retries}),
    )
    stubber = Stubber(client)
    model = BotoLanguageModel(client, _settings())
    return client, stubber, model


def _converse_response(text: str, stop: str = "end_turn") -> dict[str, object]:
    return {
        "output": {"message": {"role": "assistant", "content": [{"text": text}]}},
        "stopReason": stop,
        "usage": {"inputTokens": 10, "outputTokens": 12, "totalTokens": 22},
        "metrics": {"latencyMs": 15},
    }


def test_bedrock_success_repair_refusal_and_access_denied() -> None:
    _client, stubber, model = _bedrock()
    payload = sample_extraction()
    payload["document_ref"] = "doc-1"
    stubber.add_response("converse", _converse_response(json.dumps(payload)))
    stubber.activate()
    result = model.extract(
        document_ref="doc-1",
        page_count=1,
        transcript="<<<OCR_DOCUMENT ocr_run=x pages=1>>>\n<<<END_OCR_DOCUMENT>>>",
        lines={"p1-l001": "Jordan Sample"},
    )
    assert result.status == "succeeded"
    assert model.invocations == 1
    stubber.deactivate()

    _client, stubber, model = _bedrock()
    stubber.add_response("converse", _converse_response("{", "max_tokens"))
    stubber.add_response("converse", _converse_response(json.dumps(payload)))
    stubber.activate()
    repaired = model.extract(
        document_ref="doc-1", page_count=1, transcript="short", lines={"p1-l001": "Jordan Sample"}
    )
    assert repaired.status == "succeeded"
    assert model.invocations == 2
    stubber.deactivate()

    _client, stubber, model = _bedrock()
    stubber.add_response("converse", _converse_response("not-json", "end_turn"))
    stubber.add_response("converse", _converse_response("still-not-json", "end_turn"))
    stubber.activate()
    invalid = model.extract(document_ref="doc-1", page_count=1, transcript="short", lines={})
    assert invalid.status == "failed"
    assert invalid.error_code == "output_invalid"
    assert model.invocations == 2
    stubber.deactivate()

    _client, stubber, model = _bedrock()
    stubber.add_client_error(
        "converse",
        service_error_code="AccessDeniedException",
        service_message="no",
        http_status_code=403,
    )
    stubber.activate()
    denied = model.extract(document_ref="doc-1", page_count=1, transcript="short", lines={})
    assert denied.error_code == "bedrock_access_denied"
    assert model.invocations == 1
    stubber.deactivate()

    _client, stubber, model = _bedrock()
    stubber.add_response("converse", _converse_response("", "content_filtered"))
    stubber.activate()
    refused = model.extract(document_ref="doc-1", page_count=1, transcript="short", lines={})
    assert refused.error_code == "model_refusal"
    assert model.invocations == 1
    stubber.deactivate()

    _client, stubber, model = _bedrock(retries=3)
    stubber.add_client_error(
        "converse",
        service_error_code="ThrottlingException",
        service_message="slow",
        http_status_code=429,
    )
    stubber.activate()
    throttled = model.extract(document_ref="doc-1", page_count=1, transcript="short", lines={})
    assert throttled.error_code == "bedrock_throttled"
    assert model.invocations == 1
    assert model._client.meta.config.retries["total_max_attempts"] == 3
