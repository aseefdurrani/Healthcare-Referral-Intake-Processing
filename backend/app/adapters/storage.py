"""S3 put, head, and get. Writes send If-None-Match and a checksum, and no SSE headers."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any, Protocol

from botocore.exceptions import ClientError  # type: ignore[import-untyped]

from app.services.keys import OBJECT_KEY


class StorageError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ObjectExists(StorageError):
    def __init__(self) -> None:
        super().__init__("precondition_failed")


class ObjectMissing(StorageError):
    def __init__(self) -> None:
        super().__init__("missing")


@dataclass(frozen=True)
class ObjectHead:
    etag: str
    sse: str | None
    bucket_key: bool
    checksum_sha256: str | None
    content_length: int | None
    aws_request_id: str | None


@dataclass(frozen=True)
class PutResult:
    etag: str
    aws_request_id: str | None


def checksum_matches(sha256_hex: str, checksum_b64: str | None) -> bool:
    if not checksum_b64:
        return False
    try:
        decoded = base64.b64decode(checksum_b64, validate=True)
    except ValueError:
        return False
    try:
        expected = bytes.fromhex(sha256_hex)
    except ValueError:
        return False
    return decoded == expected


def encryption_ok(head: ObjectHead) -> bool:
    return head.sse == "aws:kms" and head.bucket_key


def _request_id(response: dict[str, Any]) -> str | None:
    value = response.get("ResponseMetadata", {}).get("RequestId")
    return value if isinstance(value, str) else None


def _code(exc: ClientError) -> tuple[str, int | None]:
    error = exc.response.get("Error", {})
    status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    name = error.get("Code", "")
    return (name if isinstance(name, str) else "", status if isinstance(status, int) else None)


def _storage_code(name: str, status: int | None) -> str:
    if name in {"AccessDenied", "InvalidKMSKeyException"} or status == 403:
        return "s3_access_denied"
    if name in {"SlowDown", "Throttling", "ThrottlingException"} or status == 429:
        return "s3_throttled"
    if status is not None and status >= 500:
        return "s3_unavailable"
    return "s3_unavailable"


class ObjectStore(Protocol):
    def put_bytes(self, key: str, body: bytes, content_type: str, sha256: bytes) -> PutResult: ...

    def head(self, key: str) -> ObjectHead: ...

    def get_bytes(self, key: str) -> bytes: ...


class S3Storage:
    """One reused S3 client. The bucket default supplies SSE-KMS."""

    def __init__(self, client: Any, bucket: str) -> None:
        self._client = client
        self._bucket = bucket

    def put_bytes(self, key: str, body: bytes, content_type: str, sha256: bytes) -> PutResult:
        self._require_key(key)
        try:
            response = self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=body,
                ContentType=content_type,
                ChecksumSHA256=base64.b64encode(sha256).decode("ascii"),
                IfNoneMatch="*",
            )
        except ClientError as exc:
            name, status = _code(exc)
            if name in {"PreconditionFailed", "412"} or status == 412:
                raise ObjectExists from exc
            raise StorageError(_storage_code(name, status)) from exc
        etag = response.get("ETag")
        return PutResult(
            etag=etag if isinstance(etag, str) else "", aws_request_id=_request_id(response)
        )

    def head(self, key: str) -> ObjectHead:
        self._require_key(key)
        try:
            response = self._client.head_object(
                Bucket=self._bucket,
                Key=key,
                ChecksumMode="ENABLED",
            )
        except ClientError as exc:
            name, status = _code(exc)
            if name in {"NoSuchKey", "NotFound", "404"} or status == 404:
                raise ObjectMissing from exc
            raise StorageError(_storage_code(name, status)) from exc
        length = response.get("ContentLength")
        checksum = response.get("ChecksumSHA256")
        sse = response.get("ServerSideEncryption")
        return ObjectHead(
            etag=response.get("ETag") if isinstance(response.get("ETag"), str) else "",
            sse=sse if isinstance(sse, str) else None,
            bucket_key=response.get("BucketKeyEnabled") is True,
            checksum_sha256=checksum if isinstance(checksum, str) else None,
            content_length=length if isinstance(length, int) else None,
            aws_request_id=_request_id(response),
        )

    def get_bytes(self, key: str) -> bytes:
        self._require_key(key)
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
        except ClientError as exc:
            name, status = _code(exc)
            if name in {"NoSuchKey", "NotFound", "404"} or status == 404:
                raise ObjectMissing from exc
            raise StorageError(_storage_code(name, status)) from exc
        body = response["Body"].read()
        return body if isinstance(body, bytes) else bytes(body)

    @staticmethod
    def _require_key(key: str) -> None:
        if OBJECT_KEY.fullmatch(key) is None:
            raise StorageError("key_collision")
