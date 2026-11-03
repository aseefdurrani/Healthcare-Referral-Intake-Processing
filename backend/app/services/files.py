"""In-memory upload checks. Nothing is stored when one of these fails."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from io import BytesIO
from typing import Any, cast

from pypdf import PdfReader
from pypdf.errors import PdfReadError

PDF_MARK = b"%PDF-"
PNG_MARK = b"\x89PNG\r\n\x1a\n"
JPEG_MARK = b"\xff\xd8\xff"

_MEDIA = {
    "pdf": "application/pdf",
    "png": "image/png",
    "jpeg": "image/jpeg",
}


class FileRejected(Exception):
    """A file that must not create a referral row or an S3 object."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class AcceptedFile:
    media_type: str
    size_bytes: int
    page_count: int
    sha256_hex: str
    sha256: bytes
    filename: str


def sanitize_filename(name: str | None, media_type: str) -> str:
    extension = {"application/pdf": "pdf", "image/png": "png", "image/jpeg": "jpg"}[media_type]
    raw = "document"
    if name:
        raw = name.replace("\\", "/").split("/")[-1]
        raw = unicodedata.normalize("NFC", raw)
        raw = "".join(ch for ch in raw if ch >= " " and ch != "\x7f")
        raw = raw.strip()
    if not raw:
        raw = f"document.{extension}"
    return raw[:255]


def _kind(data: bytes) -> str | None:
    if PDF_MARK in data[:1024]:
        return "pdf"
    if data.startswith(PNG_MARK):
        return "png"
    if data.startswith(JPEG_MARK):
        return "jpeg"
    return None


def _pdf_checks(data: bytes, max_pages: int) -> int:
    try:
        reader = PdfReader(BytesIO(data))
    except (PdfReadError, OSError, ValueError) as exc:
        raise FileRejected("file_corrupt") from exc
    if reader.is_encrypted:
        raise FileRejected("pdf_encrypted")
    try:
        root = cast(Any, reader.trailer["/Root"])
        acro = root.get("/AcroForm") if root is not None else None
        if acro is not None and "/XFA" in acro.get_object():
            raise FileRejected("pdf_xfa_unsupported")
        page_count = len(reader.pages)
    except FileRejected:
        raise
    except (PdfReadError, OSError, ValueError, KeyError, AttributeError) as exc:
        raise FileRejected("file_corrupt") from exc
    if page_count < 1:
        raise FileRejected("file_corrupt")
    if page_count > max_pages:
        raise FileRejected("too_many_pages")
    return page_count


def inspect_upload(
    data: bytes, filename: str | None, *, max_bytes: int, max_pages: int
) -> AcceptedFile:
    """Run the storage checks in order. The caller has already required a file part."""

    if len(data) > max_bytes:
        raise FileRejected("file_too_large")
    if len(data) == 0:
        raise FileRejected("file_empty")
    kind = _kind(data)
    if kind is None:
        raise FileRejected("unsupported_media_type")
    media_type = _MEDIA[kind]
    page_count = _pdf_checks(data, max_pages) if kind == "pdf" else 1
    digest = hashlib.sha256(data).digest()
    return AcceptedFile(
        media_type=media_type,
        size_bytes=len(data),
        page_count=page_count,
        sha256_hex=digest.hex(),
        sha256=digest,
        filename=sanitize_filename(filename, media_type),
    )


def attestation_accepted(value: str) -> bool:
    return bool(re.fullmatch(r"(?i)true|on|yes|1", value.strip()))
