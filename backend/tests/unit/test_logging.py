"""The formatter drops unknown fields, messages, and non-allow-listed values."""

from __future__ import annotations

import json
import logging

from tests.conftest import CANARY, build_settings

from app.core.logging import AllowListFormatter, log_event


def _record(event: str, level: int, **fields: object) -> logging.LogRecord:
    record = logging.LogRecord(
        name="cortrucent",
        level=level,
        pathname=__file__,
        lineno=1,
        msg=CANARY,
        args=(),
        exc_info=None,
    )
    record._event = event  # type: ignore[attr-defined]
    record._fields = fields  # type: ignore[attr-defined]
    return record


def test_unknown_field_and_message_are_dropped() -> None:
    formatter = AllowListFormatter(build_settings())
    line = formatter.format(
        _record(
            "http.request.completed",
            logging.INFO,
            method="GET",
            route="/api/v1/me",
            status=200,
            duration_ms=4,
            patient_name=CANARY,
        )
    )
    assert CANARY not in line
    body = json.loads(line)
    assert body["event"] == "http.request.completed"
    assert body["route"] == "/api/v1/me"
    assert "patient_name" not in body
    assert formatter.dropped_fields == 1


def test_library_warning_keeps_the_logger_name_only() -> None:
    formatter = AllowListFormatter(build_settings())
    record = logging.LogRecord(
        name="botocore.credentials",
        level=logging.WARNING,
        pathname=__file__,
        lineno=1,
        msg=CANARY,
        args=(),
        exc_info=None,
    )
    line = formatter.format(record)
    assert CANARY not in line
    body = json.loads(line)
    assert body["event"] == "library.log"
    assert body["logger"] == "botocore.credentials"


def test_bad_reason_is_not_emitted() -> None:
    formatter = AllowListFormatter(build_settings())
    line = formatter.format(
        _record("auth.denied", logging.WARNING, method="GET", route="/api/v1/me", reason=CANARY)
    )
    body = json.loads(line)
    assert "reason" not in body
    assert CANARY not in line


def test_log_event_writes_json_without_the_canary(capsys: object) -> None:
    from app.core.logging import configure_logging

    configure_logging(build_settings())
    log_event(
        logging.INFO,
        "http.request.completed",
        method="POST",
        route="/api/v1/me",
        status=401,
        duration_ms=2,
        note=CANARY,
    )
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert CANARY not in captured.out
    body = json.loads(captured.out.strip())
    assert body["status"] == 401
    assert body["service"] == "cortrucent-api"
