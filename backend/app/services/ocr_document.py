"""Pure OCR normalization and the model transcript. Identical input renders identical text."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

OCR_SCHEMA_VERSION = 1
NORMALIZER_VERSION = "1.0.0"
VERIFY_BELOW = 90.0
UNREADABLE_BELOW = 60.0
SPARSE_MAX_WORDS = 10
PAGE_LOW_MEAN = 85.0
PAGE_LOW_RATIO = 0.20

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _bbox(block: dict[str, Any]) -> list[float]:
    box = block.get("Geometry", {}).get("BoundingBox", {})
    values = [float(box.get(name, 0.0)) for name in ("Left", "Top", "Width", "Height")]
    return [round(value, 4) for value in values]


def _children(block: dict[str, Any], kind: str) -> list[str]:
    found: list[str] = []
    for relation in block.get("Relationships", []):
        if relation.get("Type") == kind:
            found.extend(relation.get("Ids", []))
    return found


def _words_text(block: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> str:
    parts: list[str] = []
    for child_id in _children(block, "CHILD"):
        child = by_id.get(child_id)
        if child is not None and child.get("BlockType") == "WORD" and child.get("Text"):
            parts.append(str(child["Text"]))
    return " ".join(parts)


def _word_stats(
    block: dict[str, Any], by_id: dict[str, dict[str, Any]]
) -> tuple[float | None, int, str]:
    confidences: list[float] = []
    types: set[str] = set()
    for child_id in _children(block, "CHILD"):
        child = by_id.get(child_id)
        if child is None or child.get("BlockType") != "WORD":
            continue
        if "Confidence" in child:
            confidences.append(float(child["Confidence"]))
        text_type = child.get("TextType")
        if isinstance(text_type, str):
            types.add(text_type)
    minimum = min(confidences) if confidences else None
    if "HANDWRITING" in types and "PRINTED" in types:
        kind = "MIXED"
    elif "HANDWRITING" in types:
        kind = "HANDWRITING"
    else:
        kind = "PRINTED"
    return minimum, len(confidences), kind


def _low_words(block: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> list[dict[str, object]]:
    found: list[dict[str, object]] = []
    for child_id in _children(block, "CHILD"):
        child = by_id.get(child_id)
        if child is None or child.get("BlockType") != "WORD":
            continue
        confidence = float(child.get("Confidence", 100))
        if confidence < VERIFY_BELOW and len(found) < 20:
            found.append({"text": str(child.get("Text", "")), "confidence": round(confidence, 1)})
    return found


def _line_payload(
    line: dict[str, Any],
    by_id: dict[str, dict[str, Any]],
    *,
    line_id: str,
    layout_role: str | None,
) -> dict[str, object]:
    minimum, count, text_type = _word_stats(line, by_id)
    return {
        "id": line_id,
        "text": str(line.get("Text", "")),
        "confidence": round(float(line.get("Confidence", 0)), 1),
        "min_word_confidence": None if minimum is None else round(minimum, 1),
        "word_count": count,
        "text_type": text_type,
        "layout_role": layout_role,
        "bbox": _bbox(line),
        "low_confidence_words": _low_words(line, by_id),
    }


def _order_lines(
    page_blocks: list[dict[str, Any]],
    by_id: dict[str, dict[str, Any]],
) -> list[tuple[dict[str, Any], str | None]]:
    """Layout blocks in response order, then any leftover lines sorted by position."""

    ordered: list[tuple[dict[str, Any], str | None]] = []
    seen: set[str] = set()
    word_to_line: dict[str, dict[str, Any]] = {}
    for block in page_blocks:
        if block.get("BlockType") != "LINE":
            continue
        for child_id in _children(block, "CHILD"):
            word_to_line[child_id] = block

    def add_line(line: dict[str, Any] | None, role: str | None) -> None:
        if line is None or line.get("BlockType") != "LINE":
            return
        identity = str(line.get("Id", id(line)))
        if identity in seen:
            return
        seen.add(identity)
        ordered.append((line, role))

    def walk(block: dict[str, Any], role: str | None) -> None:
        block_type = str(block.get("BlockType", ""))
        if block_type == "LINE":
            add_line(block, role)
            return
        if block_type == "LAYOUT_LIST":
            for child_id in _children(block, "CHILD"):
                child = by_id.get(child_id)
                if child is not None and str(child.get("BlockType", "")).startswith("LAYOUT_"):
                    walk(child, role)
            return
        if block_type.startswith("LAYOUT_"):
            layout_role = block_type.removeprefix("LAYOUT_")
            for child_id in _children(block, "CHILD"):
                child = by_id.get(child_id)
                if child is None:
                    continue
                if child.get("BlockType") == "LINE":
                    add_line(child, layout_role)
                elif str(child.get("BlockType", "")).startswith("LAYOUT_"):
                    walk(child, layout_role)
                else:
                    for word_id in _descendant_words(child, by_id):
                        add_line(word_to_line.get(word_id), layout_role)
            return

    for block in page_blocks:
        if str(block.get("BlockType", "")).startswith("LAYOUT_"):
            walk(block, None)
    leftovers = [
        block
        for block in page_blocks
        if block.get("BlockType") == "LINE" and str(block.get("Id", id(block))) not in seen
    ]
    leftovers.sort(key=lambda block: (_bbox(block)[1], _bbox(block)[0]))
    for line in leftovers:
        add_line(line, None)
    return ordered


def _descendant_words(block: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> list[str]:
    found: list[str] = []
    for child_id in _children(block, "CHILD"):
        child = by_id.get(child_id)
        if child is None:
            continue
        if child.get("BlockType") == "WORD":
            found.append(child_id)
        else:
            found.extend(_descendant_words(child, by_id))
    return found


def _page_flags(
    *,
    word_count: int,
    mean: float | None,
    low_ratio: float,
    handwriting: bool,
    warning_codes: list[str],
) -> list[dict[str, object]]:
    flags: list[dict[str, object]] = []
    if word_count == 0:
        flags.append({"code": "OCR_PAGE_NO_TEXT", "severity": "warning"})
    elif word_count < SPARSE_MAX_WORDS:
        flags.append({"code": "OCR_PAGE_SPARSE_TEXT", "severity": "info"})
    if mean is not None and (mean < PAGE_LOW_MEAN or low_ratio > PAGE_LOW_RATIO):
        flags.append({"code": "OCR_PAGE_LOW_CONFIDENCE", "severity": "warning"})
    if handwriting:
        flags.append({"code": "OCR_PAGE_HANDWRITING", "severity": "info"})
    for code in warning_codes:
        flags.append(
            {"code": "OCR_PAGE_TEXTRACT_WARNING", "severity": "warning", "detail_code": code}
        )
    return flags


def normalize(
    raw: dict[str, Any],
    *,
    ocr_run_id: str,
    document_id: str,
    feature_types: list[str],
    textract_job_id: str,
    region: str,
    preflight_page_count: int | None = None,
) -> dict[str, Any]:
    blocks = [block for block in raw.get("blocks", []) if isinstance(block, dict)]
    by_id = {str(block["Id"]): block for block in blocks if "Id" in block}
    pages_meta = int(raw.get("document_metadata", {}).get("Pages") or 0)
    page_numbers = sorted(
        {int(block.get("Page", 1)) for block in blocks if block.get("BlockType") == "PAGE"}
    )
    if not page_numbers:
        page_numbers = list(range(1, max(pages_meta, 1) + 1))
    warnings = raw.get("warnings") or []
    warnings_by_page: dict[int, list[str]] = defaultdict(list)
    for warning in warnings:
        if not isinstance(warning, dict):
            continue
        code = str(warning.get("ErrorCode") or warning.get("error_code") or "")
        for page in warning.get("Pages") or warning.get("pages") or []:
            warnings_by_page[int(page)].append(code)
    pages: list[dict[str, object]] = []
    document_flags: list[dict[str, object]] = []
    total_confidence = 0.0
    total_words = 0
    for page_number in page_numbers:
        page_blocks = [block for block in blocks if int(block.get("Page", 1)) == page_number]
        words = [block for block in page_blocks if block.get("BlockType") == "WORD"]
        confidences = [float(word.get("Confidence", 0)) for word in words]
        low = [value for value in confidences if value < VERIFY_BELOW]
        handwriting = any(word.get("TextType") == "HANDWRITING" for word in words)
        mean = round(sum(confidences) / len(confidences), 1) if confidences else None
        low_ratio = (len(low) / len(confidences)) if confidences else 0.0
        lines = []
        for index, (line, role) in enumerate(_order_lines(page_blocks, by_id), start=1):
            lines.append(
                _line_payload(line, by_id, line_id=f"p{page_number}-l{index:03d}", layout_role=role)
            )
        key_values, checkboxes = _forms(page_blocks, by_id, page_number)
        tables = _tables(page_blocks, by_id, page_number)
        page_flags = _page_flags(
            word_count=len(words),
            mean=mean,
            low_ratio=low_ratio,
            handwriting=handwriting,
            warning_codes=warnings_by_page.get(page_number, []),
        )
        for flag in page_flags:
            flag["page"] = page_number
            document_flags.append(flag)
        pages.append(
            {
                "page": page_number,
                "word_count": len(words),
                "line_count": len(lines),
                "mean_word_confidence": mean,
                "low_conf_word_ratio": round(low_ratio, 4),
                "handwriting_word_ratio": round(
                    sum(1 for word in words if word.get("TextType") == "HANDWRITING") / len(words),
                    4,
                )
                if words
                else 0.0,
                "flags": page_flags,
                "lines": lines,
                "key_values": key_values,
                "checkboxes": checkboxes,
                "tables": tables,
            }
        )
        total_confidence += sum(confidences)
        total_words += len(words)
    job_status = str(raw.get("job_status") or "SUCCEEDED")
    if job_status == "PARTIAL_SUCCESS":
        document_flags.append({"code": "OCR_PARTIAL_SUCCESS", "severity": "warning"})
    if preflight_page_count is not None and pages_meta and preflight_page_count != pages_meta:
        document_flags.append({"code": "OCR_PAGE_COUNT_MISMATCH", "severity": "warning"})
    if total_words == 0:
        document_flags.append({"code": "OCR_DOCUMENT_NO_TEXT", "severity": "blocking"})
    textract_warnings = []
    for warning in warnings:
        if isinstance(warning, dict):
            textract_warnings.append(
                {
                    "error_code": str(warning.get("ErrorCode") or warning.get("error_code") or ""),
                    "pages": list(warning.get("Pages") or warning.get("pages") or []),
                }
            )
    return {
        "ocr_schema_version": OCR_SCHEMA_VERSION,
        "normalizer_version": NORMALIZER_VERSION,
        "ocr_run_id": ocr_run_id,
        "document_id": document_id,
        "engine": {
            "provider": "amazon-textract",
            "api": "StartDocumentAnalysis",
            "feature_types": list(feature_types),
            "textract_job_id": textract_job_id,
            "model_version": raw.get("model_version"),
            "region": region,
        },
        "status": "SUCCEEDED" if job_status == "PARTIAL_SUCCESS" else job_status,
        "page_count": len(pages),
        "pages": pages,
        "flags": document_flags,
        "textract_warnings": textract_warnings,
        "mean_word_confidence": round(total_confidence / total_words, 2) if total_words else None,
    }


def _forms(
    page_blocks: list[dict[str, Any]],
    by_id: dict[str, dict[str, Any]],
    page_number: int,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    keys = [
        block
        for block in page_blocks
        if block.get("BlockType") == "KEY_VALUE_SET" and "KEY" in (block.get("EntityTypes") or [])
    ]
    keys.sort(key=lambda block: (_bbox(block)[1], _bbox(block)[0]))
    key_values: list[dict[str, object]] = []
    checkboxes: list[dict[str, object]] = []
    for key in keys:
        value_ids = _children(key, "VALUE")
        value = by_id.get(value_ids[0]) if value_ids else None
        if value is not None and _has_selection(value, by_id):
            selection = _first_selection(value, by_id)
            checkboxes.append(
                {
                    "id": f"p{page_number}-cb{len(checkboxes) + 1:03d}",
                    "label_text": _words_text(key, by_id),
                    "status": str(selection.get("SelectionStatus") or "NOT_SELECTED"),
                    "confidence": round(float(selection.get("Confidence", 0)), 1),
                    "source": "form",
                    "bbox": _bbox(selection),
                }
            )
            continue
        value_text = _words_text(value, by_id) if value is not None else ""
        _minimum, _count, _kind = (
            _word_stats(value, by_id) if value is not None else (None, 0, "PRINTED")
        )
        key_values.append(
            {
                "id": f"p{page_number}-kv{len(key_values) + 1:03d}",
                "key_text": _words_text(key, by_id),
                "value_text": value_text,
                "key_confidence": round(float(key.get("Confidence", 0)), 1),
                "value_confidence": round(float(value.get("Confidence", 0)), 1) if value else None,
                "value_min_word_confidence": None if _minimum is None else round(_minimum, 1),
                "key_line_ids": [],
                "value_line_ids": [],
                "value_bbox": _bbox(value) if value is not None else _bbox(key),
            }
        )
    return key_values, checkboxes


def _has_selection(block: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> bool:
    return _first_selection(block, by_id).get("BlockType") == "SELECTION_ELEMENT"


def _first_selection(block: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    for child_id in _children(block, "CHILD"):
        child = by_id.get(child_id)
        if child is not None and child.get("BlockType") == "SELECTION_ELEMENT":
            return child
    return {}


def _tables(
    page_blocks: list[dict[str, Any]],
    by_id: dict[str, dict[str, Any]],
    page_number: int,
) -> list[dict[str, object]]:
    tables: list[dict[str, object]] = []
    for index, table in enumerate(
        (block for block in page_blocks if block.get("BlockType") == "TABLE"),
        start=1,
    ):
        table_id = f"p{page_number}-t{index:02d}"
        cells: list[dict[str, object]] = []
        for cell in (
            by_id[child_id]
            for child_id in _children(table, "CHILD")
            if child_id in by_id and by_id[child_id].get("BlockType") == "CELL"
        ):
            row = int(cell.get("RowIndex", 1))
            col = int(cell.get("ColumnIndex", 1))
            selection = _first_selection(cell, by_id)
            cells.append(
                {
                    "id": f"{table_id}-r{row:02d}c{col:02d}",
                    "row": row,
                    "col": col,
                    "row_span": int(cell.get("RowSpan", 1)),
                    "col_span": int(cell.get("ColumnSpan", 1)),
                    "text": _words_text(cell, by_id),
                    "confidence": round(float(cell.get("Confidence", 0)), 1),
                    "is_header": "COLUMN_HEADER" in (cell.get("EntityTypes") or []),
                    "selection_status": selection.get("SelectionStatus"),
                }
            )
        cells.sort(key=lambda cell: (int(str(cell["row"])), int(str(cell["col"]))))
        tables.append(
            {
                "id": table_id,
                "confidence": round(float(table.get("Confidence", 0)), 1),
                "bbox": _bbox(table),
                "cells": cells,
            }
        )
    return tables


def _clean(text: str) -> str:
    cleaned = _CONTROL.sub("", text)
    cleaned = cleaned.replace("<<<END_OCR_DOCUMENT>>>", "[document-tag-removed]")
    return cleaned.replace("<<<OCR_DOCUMENT", "[document-tag-removed]")


def _marker(line: dict[str, object]) -> str:
    minimum = line.get("min_word_confidence")
    marker = ""
    if isinstance(minimum, int | float) and float(minimum) < UNREADABLE_BELOW:
        marker = "|UNREADABLE"
    elif isinstance(minimum, int | float) and float(minimum) < VERIFY_BELOW:
        marker = "|LOWCONF"
    if line.get("text_type") in {"HANDWRITING", "MIXED"}:
        marker += "|HW"
    return marker


def _form_text(item: dict[str, Any]) -> str:
    value = _clean(str(item.get("value_text", "")))
    shown = '"" (empty)' if value == "" else f'"{value}"'
    return f'"{_clean(str(item.get("key_text", "")))}" = {shown}'


def _checkbox_text(item: dict[str, Any]) -> str:
    return f'"{_clean(str(item.get("label_text", "")))}" = {item.get("status")}'


def render_for_llm(document: dict[str, Any]) -> str:
    pages = document.get("pages") or []
    run_id = document.get("ocr_run_id")
    page_count = document.get("page_count")
    lines = [f"<<<OCR_DOCUMENT ocr_run={run_id} pages={page_count}>>>"]
    for page in pages:
        lines.append(f"=== PAGE {page['page']} ===")
        for line in page.get("lines") or []:
            lines.append(f"[{line['id']}{_marker(line)}] {_clean(str(line.get('text', '')))}")
        key_values = page.get("key_values") or []
        if key_values:
            lines.append(f"--- FORM FIELDS (page {page['page']}) ---")
            for item in key_values:
                lines.append(f'[{item["id"]}] {_form_text(item)}')
        boxes = page.get("checkboxes") or []
        if boxes:
            lines.append(f"--- CHECKBOXES (page {page['page']}) ---")
            for box in boxes:
                lines.append(f'[{box["id"]}] {_checkbox_text(box)}')
        for table in page.get("tables") or []:
            lines.append(f"--- TABLE {table['id']} (page {page['page']}) ---")
            lines.extend(_table_lines(table))
    lines.append("<<<END_OCR_DOCUMENT>>>")
    return "\n".join(lines)


def _table_lines(table: dict[str, object]) -> list[str]:
    cells = table.get("cells") or []
    if not isinstance(cells, list) or not cells:
        return []
    rows: dict[int, dict[int, str]] = defaultdict(dict)
    for cell in cells:
        if isinstance(cell, dict):
            rows[int(cell["row"])][int(cell["col"])] = _clean(str(cell.get("text", "")))
    rendered: list[str] = []
    for row_index in sorted(rows):
        columns = rows[row_index]
        width = max(columns) if columns else 0
        rendered.append(
            "| " + " | ".join(columns.get(col, "") for col in range(1, width + 1)) + " |"
        )
    return rendered


def element_index(document: dict[str, Any]) -> dict[str, dict[str, object]]:
    """Id to text, page, bbox, and the confidence used for readability tiers."""

    index: dict[str, dict[str, object]] = {}
    for page in document.get("pages") or []:
        page_number = int(page["page"])
        for line in page.get("lines") or []:
            index[str(line["id"])] = {
                "text": _clean(str(line.get("text", ""))),
                "page": page_number,
                "bbox": line.get("bbox"),
                "confidence": line.get("min_word_confidence"),
                "handwriting": line.get("text_type") in {"HANDWRITING", "MIXED"},
            }
        for item in page.get("key_values") or []:
            index[str(item["id"])] = {
                "text": _form_text(item),
                "page": page_number,
                "bbox": item.get("value_bbox"),
                "confidence": item.get("value_min_word_confidence"),
                "handwriting": False,
            }
        for box in page.get("checkboxes") or []:
            index[str(box["id"])] = {
                "text": _checkbox_text(box),
                "page": page_number,
                "bbox": box.get("bbox"),
                "confidence": box.get("confidence"),
                "handwriting": False,
            }
        for table in page.get("tables") or []:
            for cell in table.get("cells") or []:
                index[str(cell["id"])] = {
                    "text": str(cell.get("text", "")),
                    "page": page_number,
                    "bbox": table.get("bbox"),
                    "confidence": cell.get("confidence"),
                    "handwriting": False,
                }
    return index


def line_map(document: dict[str, Any]) -> dict[str, str]:
    return {key: str(value["text"]) for key, value in element_index(document).items()}


def flag_codes(document: dict[str, Any]) -> list[str]:
    codes: list[str] = []
    for flag in document.get("flags") or []:
        code = flag.get("code")
        if isinstance(code, str) and code not in codes:
            codes.append(code)
    return codes
