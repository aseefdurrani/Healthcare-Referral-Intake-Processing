"""Textract async analysis. GetDocumentAnalysis has no paginator, so pages are followed here."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from botocore.exceptions import (  # type: ignore[import-untyped]
    ClientError,
    ConnectTimeoutError,
    EndpointConnectionError,
    ReadTimeoutError,
)

from app.core.config import Settings

OCR_CONFIG_VERSION = "1"
FEATURES = ("LAYOUT", "FORMS", "TABLES")
POLL_INTERVAL_SECONDS = 3.0
POLL_TIMEOUT_SECONDS = 180.0
JOB_TTL = timedelta(days=7)
_TOKEN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_TRANSIENT = {
    "ThrottlingException",
    "ProvisionedThroughputExceededException",
    "LimitExceededException",
    "InternalServerError",
}
_DOCUMENT = {"BadDocumentException", "UnsupportedDocumentException", "DocumentTooLargeException"}
_CONFIG = {"InvalidS3ObjectException", "AccessDeniedException", "InvalidKMSKeyException"}


def client_request_token(document_id_hex: str, attempt: int) -> str:
    token = f"ocr-{document_id_hex}-c{OCR_CONFIG_VERSION}-a{attempt}"
    if _TOKEN.fullmatch(token) is None:
        raise ValueError("client request token")
    return token


@dataclass
class StartResult:
    job_id: str
    aws_request_id: str | None


class TextractFailure(Exception):
    def __init__(self, result: OcrCollect) -> None:
        super().__init__(result.error_code or "OCR_FAILED")
        self.result = result


@dataclass
class OcrCollect:
    kind: str
    blocks: list[dict[str, Any]] = field(default_factory=list)
    pages: int | None = None
    model_version: str | None = None
    warnings: list[dict[str, Any]] = field(default_factory=list)
    job_status: str | None = None
    error_code: str | None = None
    error_category: str | None = None
    status_message: str | None = None
    retryable: bool = False
    poll_count: int = 0
    aws_request_id: str | None = None
    aws_error_code: str | None = None


def _request_id(response: dict[str, Any]) -> str | None:
    value = response.get("ResponseMetadata", {}).get("RequestId")
    return value if isinstance(value, str) else None


def _error_name(exc: ClientError) -> str:
    name = exc.response.get("Error", {}).get("Code", "")
    return name if isinstance(name, str) else "ClientError"


class BotoTextract:
    def __init__(self, client: Any, settings: Settings) -> None:
        self._client = client
        self._settings = settings

    def start_job(
        self,
        *,
        bucket: str,
        key: str,
        token: str,
        job_tag: str,
        features: list[str],
    ) -> StartResult:
        if _TOKEN.fullmatch(token) is None:
            raise ValueError("client request token")
        try:
            response = self._client.start_document_analysis(
                DocumentLocation={"S3Object": {"Bucket": bucket, "Name": key}},
                FeatureTypes=features,
                ClientRequestToken=token,
                JobTag=job_tag,
            )
        except (ClientError, ConnectTimeoutError, ReadTimeoutError, EndpointConnectionError) as exc:
            raise TextractFailure(self._as_failure(exc)) from exc
        job_id = response.get("JobId")
        if not isinstance(job_id, str) or not job_id:
            raise TextractFailure(
                OcrCollect(
                    kind="failed",
                    error_code="OCR_FAILED",
                    error_category="internal",
                    retryable=False,
                )
            )
        return StartResult(job_id=job_id, aws_request_id=_request_id(response))

    def collect_job(
        self, job_id: str, *, worker_deadline: datetime, now: Any, sleep: Any
    ) -> OcrCollect:
        """Poll until a terminal status, 180 seconds, or the worker budget."""

        deadline = min(now() + timedelta(seconds=POLL_TIMEOUT_SECONDS), worker_deadline)
        polls = 0
        token: str | None = None
        blocks: list[dict[str, Any]] = []
        status = "IN_PROGRESS"
        pages: int | None = None
        model_version: str | None = None
        warnings: list[dict[str, Any]] = []
        request_id: str | None = None
        message: str | None = None
        while True:
            if now() >= worker_deadline:
                return OcrCollect(kind="yield_worker", poll_count=polls, aws_request_id=request_id)
            kwargs: dict[str, object] = {"JobId": job_id, "MaxResults": 1000}
            if token:
                kwargs["NextToken"] = token
            try:
                response = self._client.get_document_analysis(**kwargs)
            except (
                ClientError,
                ConnectTimeoutError,
                ReadTimeoutError,
                EndpointConnectionError,
            ) as exc:
                failure = self._as_failure(exc)
                if failure.error_category == "transient":
                    if now() >= deadline:
                        return OcrCollect(
                            kind="yield_ocr",
                            poll_count=polls,
                            aws_request_id=request_id,
                        )
                    sleep(POLL_INTERVAL_SECONDS)
                    continue
                failure.poll_count = polls
                return failure
            polls += 1
            request_id = _request_id(response) or request_id
            status = str(response.get("JobStatus") or status)
            if response.get("DocumentMetadata", {}).get("Pages") is not None:
                pages = int(response["DocumentMetadata"]["Pages"])
            if response.get("AnalyzeDocumentModelVersion"):
                model_version = str(response["AnalyzeDocumentModelVersion"])
            if response.get("Warnings"):
                warnings = list(response["Warnings"])
            if response.get("StatusMessage"):
                message = str(response["StatusMessage"])[:500]
            if status in {"SUCCEEDED", "PARTIAL_SUCCESS"}:
                blocks.extend(
                    block for block in response.get("Blocks", []) if isinstance(block, dict)
                )
                token = response.get("NextToken")
                if token:
                    continue
                return OcrCollect(
                    kind="partial" if status == "PARTIAL_SUCCESS" else "succeeded",
                    blocks=blocks,
                    pages=pages,
                    model_version=model_version,
                    warnings=warnings,
                    job_status=status,
                    poll_count=polls,
                    aws_request_id=request_id,
                )
            if status == "FAILED":
                return OcrCollect(
                    kind="failed",
                    job_status=status,
                    error_code="OCR_FAILED",
                    error_category="document",
                    status_message=message,
                    retryable=False,
                    poll_count=polls,
                    aws_request_id=request_id,
                    pages=pages,
                )
            if now() >= deadline:
                kind = "yield_worker" if now() >= worker_deadline else "yield_ocr"
                return OcrCollect(
                    kind=kind, poll_count=polls, aws_request_id=request_id, job_status=status
                )
            sleep(POLL_INTERVAL_SECONDS)

    def _as_failure(self, exc: Exception) -> OcrCollect:
        if isinstance(exc, ClientError):
            name = _error_name(exc)
        else:
            name = type(exc).__name__
        if name in _DOCUMENT:
            return OcrCollect(
                kind="failed",
                error_code="OCR_FAILED",
                error_category="document",
                retryable=False,
                aws_error_code=name,
            )
        if name in _CONFIG:
            return OcrCollect(
                kind="failed",
                error_code="OCR_FAILED",
                error_category="config",
                retryable=False,
                aws_error_code=name,
            )
        if name in {
            "InvalidParameterException",
            "IdempotentParameterMismatchException",
            "InvalidJobIdException",
        }:
            return OcrCollect(
                kind="failed",
                error_code="OCR_FAILED",
                error_category="internal",
                retryable=False,
                aws_error_code=name,
            )
        return OcrCollect(
            kind="failed",
            error_code="OCR_FAILED",
            error_category="transient",
            retryable=True,
            aws_error_code=name
            if name in _TRANSIENT or name.endswith("Timeout") or name.endswith("Error")
            else None,
        )
