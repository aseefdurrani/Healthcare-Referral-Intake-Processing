"""The only two object-key templates. Client input never becomes part of a key."""

from __future__ import annotations

import re
import uuid

_UUID4 = r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
SOURCE_KEY = re.compile(rf"^referrals/{_UUID4}/documents/{_UUID4}/source\.(pdf|png|jpg)$")
OCR_KEY = re.compile(
    rf"^referrals/{_UUID4}/documents/{_UUID4}/ocr/{_UUID4}/textract-response\.json\.gz$"
)
OBJECT_KEY = re.compile(
    rf"^referrals/{_UUID4}/documents/{_UUID4}/"
    rf"(source\.(pdf|png|jpg)|ocr/{_UUID4}/textract-response\.json\.gz)$"
)

_EXTENSIONS = {"application/pdf": "pdf", "image/png": "png", "image/jpeg": "jpg"}


def extension_for(media_type: str) -> str:
    try:
        return _EXTENSIONS[media_type]
    except KeyError as exc:
        raise ValueError("media type") from exc


def _require_uuid4(value: uuid.UUID) -> str:
    if value.version != 4:
        raise ValueError("identifier")
    return str(value)


def source_key(referral_id: uuid.UUID, document_id: uuid.UUID, media_type: str) -> str:
    key = (
        f"referrals/{_require_uuid4(referral_id)}/documents/"
        f"{_require_uuid4(document_id)}/source.{extension_for(media_type)}"
    )
    if SOURCE_KEY.fullmatch(key) is None:
        raise ValueError("source key")
    return key


def ocr_key(referral_id: uuid.UUID, document_id: uuid.UUID, ocr_run_id: uuid.UUID) -> str:
    key = (
        f"referrals/{_require_uuid4(referral_id)}/documents/"
        f"{_require_uuid4(document_id)}/ocr/{_require_uuid4(ocr_run_id)}/"
        "textract-response.json.gz"
    )
    if OCR_KEY.fullmatch(key) is None:
        raise ValueError("ocr key")
    return key
