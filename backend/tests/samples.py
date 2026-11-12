"""Synthetic files and a minimal extraction used by the workflow tests."""

from __future__ import annotations

from decimal import Decimal
from io import BytesIO
from typing import Any

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app.ai.contracts import AiRunResult
from app.schemas.ai import ReferralExtractionV1

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753"
    "de0000000c4944415408d763f8cfc000000301010018dd8db40000000049454e44ae426082"
)
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 16
LINE = "Jordan Sample"


def pdf_bytes(pages: int, *, password: str | None = None) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=72, height=72)
    if password is not None:
        writer.encrypt(password)
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def xfa_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    stream = DecodedStreamObject()
    stream.set_data(b"<xdp:xdp></xdp:xdp>")
    form = DictionaryObject()
    form[NameObject("/XFA")] = stream
    writer._root_object[NameObject("/AcroForm")] = form
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _empty() -> dict[str, Any]:
    return {"value": "", "status": "not_found", "evidence": [], "note": ""}


def _priority() -> dict[str, Any]:
    return {"label": "not_marked", **_empty()}


def _requested() -> dict[str, Any]:
    return {"label": "none_stated", **_empty()}


def sample_extraction() -> dict[str, Any]:
    evidence = [{"line_id": "p1-l001", "quote": LINE}]
    payload = {
        "document_ref": "pending",
        "patient": {
            "full_name": {
                "value": LINE,
                "status": "explicit",
                "evidence": evidence,
                "note": "",
            },
            "date_of_birth": _empty(),
            "phone": _empty(),
            "email": _empty(),
            "address": _empty(),
            "identifiers": [],
        },
        "referring_clinician": {
            "name": _empty(),
            "organization": _empty(),
            "phone": _empty(),
            "fax": _empty(),
            "npi": _empty(),
        },
        "referral": {
            "referral_date": _empty(),
            "reason": _empty(),
            "sender_priority": _priority(),
            "requested_specialty": _requested(),
            "diagnosis_codes": [],
        },
        "specialty_suggestion": {
            "label": "cardiology",
            "other_specialty_name": "",
            "candidates": [],
            "relation_to_request": "no_request_stated",
            "rationale": "The line names a cardiac review.",
            "evidence": evidence,
        },
        "document_flags": {
            "multiple_patients_suspected": False,
            "embedded_instructions_detected": False,
            "illegible_or_incomplete_content": False,
            "not_a_referral": False,
        },
        "issues": [],
    }
    return ReferralExtractionV1.model_validate(payload).model_dump()


def sample_result() -> AiRunResult:
    return AiRunResult(
        status="succeeded",
        attempts=1,
        stop_reason="end_turn",
        input_tokens=20,
        output_tokens=30,
        estimated_cost_usd=Decimal("0.000170"),
        output=sample_extraction(),
        model_id="gpt-6.1-terra",
        provider_request_id="req_openai_sample",
    )


def sample_blocks() -> list[dict[str, Any]]:
    box = {"Left": 0.1, "Top": 0.2, "Width": 0.3, "Height": 0.02}
    return [
        {"BlockType": "PAGE", "Id": "page-1", "Page": 1},
        {
            "BlockType": "LINE",
            "Id": "line-1",
            "Page": 1,
            "Text": LINE,
            "Confidence": 99.0,
            "Geometry": {"BoundingBox": box},
            "Relationships": [{"Type": "CHILD", "Ids": ["word-1"]}],
        },
        {
            "BlockType": "WORD",
            "Id": "word-1",
            "Page": 1,
            "Text": LINE,
            "Confidence": 99.0,
            "TextType": "PRINTED",
            "Geometry": {"BoundingBox": box},
        },
    ]
