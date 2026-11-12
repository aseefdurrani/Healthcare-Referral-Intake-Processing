"""Quotes must be verified against exactly the text supplied to the model."""

from app.services.grounding import _warnings_for_evidence
from app.services.ocr_document import line_map, render_for_llm


def test_form_and_checkbox_quotes_match_model_transcript() -> None:
    document = {
        "ocr_run_id": "test",
        "page_count": 1,
        "pages": [
            {
                "page": 1,
                "lines": [{"id": "p1-l001", "text": "Ordinary line"}],
                "key_values": [
                    {"id": "p1-kv001", "key_text": "PHONE *", "value_text": "202-555-0101"},
                    {"id": "p1-kv002", "key_text": "EMAIL", "value_text": ""},
                ],
                "checkboxes": [{"id": "p1-cb001", "label_text": "Routine", "status": "SELECTED"}],
            }
        ],
    }
    transcript = render_for_llm(document)
    sources = line_map(document)
    for source_id, quote in sources.items():
        assert f"[{source_id}] {quote}" in transcript
        assert (
            _warnings_for_evidence(
                "patient.phone", [{"line_id": source_id, "quote": quote}], sources
            )
            == []
        )
    assert _warnings_for_evidence(
        "patient.phone", [{"line_id": "p1-kv001", "quote": '"PHONE *" = "202-555-9999"'}], sources
    ) == [{"path": "patient.phone", "code": "evidence_unverified"}]
    assert _warnings_for_evidence(
        "patient.phone", [{"line_id": "p9-kv999", "quote": "202-555-0101"}], sources
    ) == [{"path": "patient.phone", "code": "evidence_unknown_line"}]
