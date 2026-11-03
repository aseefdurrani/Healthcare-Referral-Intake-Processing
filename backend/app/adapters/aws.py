"""One boto3 session and the three clients the workflow reuses."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import boto3  # type: ignore[import-untyped]
from botocore.config import Config  # type: ignore[import-untyped]

from app.core.config import Settings

CONNECT_TIMEOUT = 5
S3_READ_TIMEOUT = 30
TEXTRACT_READ_TIMEOUT = 30


def _config(settings: Settings, read_timeout: int, *, s3: bool = False) -> Config:
    kwargs: dict[str, object] = {
        "connect_timeout": CONNECT_TIMEOUT,
        "read_timeout": read_timeout,
        "retries": {"mode": "standard", "total_max_attempts": 3},
        "user_agent_extra": f"cortrucent-intake/{settings.app_version}",
    }
    if s3:
        kwargs["signature_version"] = "s3v4"
    return Config(**kwargs)


@dataclass
class AwsClients:
    s3: Any
    textract: Any
    bedrock: Any


def build_aws(settings: Settings) -> AwsClients:
    session_kwargs: dict[str, str] = {"region_name": settings.aws_region}
    if settings.aws_profile:
        session_kwargs["profile_name"] = settings.aws_profile
    session = boto3.Session(**session_kwargs)
    return AwsClients(
        s3=session.client("s3", config=_config(settings, S3_READ_TIMEOUT, s3=True)),
        textract=session.client("textract", config=_config(settings, TEXTRACT_READ_TIMEOUT)),
        bedrock=session.client(
            "bedrock-runtime",
            config=_config(settings, settings.bedrock_read_timeout_seconds),
        ),
    )
