"""Put an object once and require the bucket's SSE-S3 default."""

from __future__ import annotations

from app.adapters.storage import (
    ObjectExists,
    ObjectHead,
    ObjectStore,
    StorageError,
    checksum_matches,
    encryption_ok,
)


def write_once(
    storage: ObjectStore,
    *,
    key: str,
    body: bytes,
    content_type: str,
    sha256: bytes,
    sha256_hex: str,
) -> ObjectHead:
    try:
        storage.put_bytes(key, body, content_type, sha256)
    except ObjectExists:
        pass
    head = storage.head(key)
    if not checksum_matches(sha256_hex, head.checksum_sha256):
        raise StorageError("key_collision" if head.checksum_sha256 else "s3_checksum_mismatch")
    if not encryption_ok(head):
        raise StorageError("s3_encryption_mismatch")
    return head
