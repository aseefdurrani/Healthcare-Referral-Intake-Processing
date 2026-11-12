"""In-memory stand-ins for S3, Textract, and the model call."""

from __future__ import annotations

import base64
from typing import Any

from app.adapters.ocr import OcrCollect, StartResult, TextractFailure
from app.adapters.storage import (
    ObjectExists,
    ObjectHead,
    ObjectMissing,
    PutResult,
    StorageError,
)
from app.ai.contracts import AiRunResult


class MemoryStorage:
    def __init__(self, *, sse: str = "aws:kms", bucket_key: bool = True) -> None:
        self.objects: dict[str, dict[str, Any]] = {}
        self.sse = sse
        self.bucket_key = bucket_key
        self.puts: list[str] = []

    def put_bytes(self, key: str, body: bytes, content_type: str, sha256: bytes) -> PutResult:
        if key in self.objects:
            raise ObjectExists()
        self.objects[key] = {"body": body, "content_type": content_type, "sha256": sha256}
        self.puts.append(key)
        return PutResult(etag='"etag"', aws_request_id="aws-put")

    def head(self, key: str) -> ObjectHead:
        obj = self.objects.get(key)
        if obj is None:
            raise ObjectMissing()
        return ObjectHead(
            etag='"etag"',
            sse=self.sse,
            bucket_key=self.bucket_key,
            checksum_sha256=base64.b64encode(obj["sha256"]).decode("ascii"),
            content_length=len(obj["body"]),
            aws_request_id="aws-head",
        )

    def get_bytes(self, key: str) -> bytes:
        obj = self.objects.get(key)
        if obj is None:
            raise ObjectMissing()
        body = obj["body"]
        if not isinstance(body, bytes):
            raise StorageError("s3_unavailable")
        return body


class ScriptedTextract:
    def __init__(self) -> None:
        self.starts: list[dict[str, Any]] = []
        self.collects: list[str] = []
        self.results: list[OcrCollect] = []
        self.blocks: list[dict[str, Any]] = []
        self.fail_start: TextractFailure | None = None

    def start_job(self, **kwargs: Any) -> StartResult:
        self.starts.append(kwargs)
        if self.fail_start is not None:
            raise self.fail_start
        return StartResult(job_id="job-1", aws_request_id="aws-start")

    def collect_job(self, job_id: str, **_kwargs: Any) -> OcrCollect:
        self.collects.append(job_id)
        if self.results:
            return self.results.pop(0)
        return OcrCollect(
            kind="succeeded",
            blocks=self.blocks,
            pages=1,
            model_version="test",
            job_status="SUCCEEDED",
            poll_count=1,
            aws_request_id="aws-get",
        )


class ScriptedModel:
    def __init__(self, result: AiRunResult) -> None:
        self.result = result
        self.calls = 0
        self.kwargs: dict[str, Any] = {}

    def extract(self, **kwargs: Any) -> AiRunResult:
        self.calls += 1
        self.kwargs = kwargs
        return self.result
