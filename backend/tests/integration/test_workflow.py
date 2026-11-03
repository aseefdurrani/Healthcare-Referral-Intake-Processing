"""Phase 3 workflow against PostgreSQL 18 and in-memory AWS."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from tests.conftest import STAFF_SUB, assert_problem, build_settings, make_token
from tests.fakes import MemoryStorage, ScriptedModel, ScriptedTextract
from tests.integration.conftest import Database
from tests.samples import JPEG, LINE, PNG, pdf_bytes, sample_blocks, sample_result

from app.adapters.llm import AiRunResult
from app.adapters.ocr import OcrCollect
from app.adapters.storage import ObjectExists, StorageError
from app.core.auth import JwkClient
from app.db.models import AuditEvent, ProcessingRun, Referral, ReferralField, ValidationFlag
from app.main import create_app
from app.services.objects import write_once


def _purge(database: Database) -> None:
    with database.admin_engine.begin() as connection:
        ids = list(
            connection.execute(text("SELECT id FROM referrals ORDER BY created_at DESC")).scalars()
        )
        for referral_id in ids:
            connection.execute(
                text(
                    "UPDATE referrals SET possible_duplicate_of_referral_id = NULL "
                    "WHERE possible_duplicate_of_referral_id = :id"
                ),
                {"id": referral_id},
            )
            connection.execute(
                text(
                    "UPDATE documents SET possible_duplicate_of_document_id = NULL "
                    "WHERE referral_id <> :id AND possible_duplicate_of_document_id IN "
                    "(SELECT id FROM documents WHERE referral_id = :id)"
                ),
                {"id": referral_id},
            )
            connection.execute(text("SELECT demo_purge_referral(:id)"), {"id": referral_id})


def _count(database: Database) -> int:
    with database.app_engine.connect() as connection:
        return int(connection.execute(text("SELECT count(*) FROM referrals")).scalar_one())


class _Exists(MemoryStorage):
    def put_bytes(self, key: str, body: bytes, content_type: str, sha256: bytes) -> object:
        self.objects[key] = {"body": body, "content_type": content_type, "sha256": sha256}
        raise ObjectExists()


@pytest.fixture
def workflow(
    database: Database,
    material: object,
    jwk_client: JwkClient,
) -> Iterator[tuple[TestClient, MemoryStorage, ScriptedTextract, ScriptedModel]]:
    _purge(database)
    settings = build_settings(s3_bucket_name="referrals-test-bucket")
    storage = MemoryStorage()
    textract = ScriptedTextract()
    textract.blocks = sample_blocks()
    model = ScriptedModel(sample_result())
    app = create_app(
        settings,
        jwk_client=jwk_client,
        engine=database.app_engine,
        storage=storage,
        textract=textract,
        bedrock=model,
    )
    token = make_token(material)  # type: ignore[arg-type]
    with TestClient(app) as client:
        client.headers["Authorization"] = f"Bearer {token}"
        yield client, storage, textract, model
    _purge(database)


def _upload(
    client: TestClient,
    payload: bytes,
    name: str,
    *,
    key: str | None = None,
    content_type: str = "application/pdf",
) -> object:
    headers = {}
    if key is not None:
        headers["Idempotency-Key"] = key
    return client.post(
        "/api/v1/referrals",
        files={"file": (name, payload, content_type)},
        data={"attestation": "true"},
        headers=headers,
    )


def _drain(client: TestClient) -> None:
    runner = client.app.state.runner
    if runner is not None:
        runner.drain()


def _approve(version: int, absent: list[str]) -> dict[str, object]:
    return {
        "version": version,
        "destination_specialty": {"code": "cardiology"},
        "sender_priority": "routine",
        "not_on_document": absent,
        "acknowledgements": [],
    }


def test_openapi_has_workflow_routes_and_no_transmit(jwk_client: JwkClient) -> None:
    app = create_app(build_settings(app_env="local"))
    paths = app.openapi()["paths"]
    expected = {
        "/healthz",
        "/api/v1/me",
        "/api/v1/referrals",
        "/api/v1/referrals/{id}",
        "/api/v1/referrals/{id}/status",
        "/api/v1/referrals/{id}/document",
        "/api/v1/referrals/{id}/approve",
        "/api/v1/referrals/{id}/reject",
        "/api/v1/referrals/{id}/retry",
    }
    assert expected <= set(paths)
    assert "transmit" not in " ".join(paths)


def test_rejected_uploads_leave_no_row(
    workflow: tuple[TestClient, MemoryStorage, ScriptedTextract, ScriptedModel],
    database: Database,
) -> None:
    client, storage, _textract, _model = workflow
    before = _count(database)
    cases = [
        (pdf_bytes(11), "eleven.pdf", 422, "TOO_MANY_PAGES"),
        (b"x" * 10_000_001, "over.pdf", 413, "FILE_TOO_LARGE"),
        (b"", "empty.pdf", 422, "EMPTY_FILE"),
        (pdf_bytes(1, password="secret"), "locked.pdf", 422, "ENCRYPTED_PDF"),
        (b"%PDF-1.4\ncut", "cut.pdf", 422, "UNREADABLE_PDF"),
        (b"not-a-file", "notes.pdf", 415, "UNSUPPORTED_FILE_TYPE"),
    ]
    for payload, name, status, code in cases:
        response = _upload(client, payload, name)
        assert_problem(response, status, code)
    assert _count(database) == before
    assert storage.puts == []


def test_valid_upload_reaches_review(
    workflow: tuple[TestClient, MemoryStorage, ScriptedTextract, ScriptedModel],
) -> None:
    client, storage, textract, model = workflow
    response = _upload(client, pdf_bytes(2), "synthetic-referral.pdf")
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "uploaded"
    assert response.headers["location"].endswith(body["id"])
    _drain(client)
    detail = client.get(f"/api/v1/referrals/{body['id']}")
    assert detail.status_code == 200
    payload = detail.json()
    assert payload["status"] == "needs_review"
    name = next(field for field in payload["fields"] if field["key"] == "patient.full_name")
    assert name["source"]["element_ids"] == ["p1-l001"]
    assert name["suggested_value"] == LINE
    assert storage.puts[0].endswith("/source.pdf")
    assert "referrals/" not in response.text
    assert textract.starts[0]["token"].startswith("ocr-")
    assert model.calls == 1


def test_duplicate_and_idempotency(
    workflow: tuple[TestClient, MemoryStorage, ScriptedTextract, ScriptedModel],
) -> None:
    client, storage, _textract, _model = workflow
    first = _upload(client, pdf_bytes(1), "one.pdf")
    _drain(client)
    second = _upload(client, pdf_bytes(1), "two.pdf")
    assert second.status_code == 202
    assert second.json()["possible_duplicate_of"] == first.json()["id"]
    key = str(uuid.uuid4())
    replay = _upload(client, JPEG, "photo.jpg", key=key, content_type="image/jpeg")
    _drain(client)
    again = _upload(client, JPEG, "photo.jpg", key=key, content_type="image/jpeg")
    assert again.json()["id"] == replay.json()["id"]
    puts = len(storage.puts)
    conflict = _upload(client, PNG, "scan.png", key=key, content_type="image/png")
    assert_problem(conflict, 409, "IDEMPOTENCY_KEY_REUSED")
    assert len(storage.puts) == puts


def test_png_named_pdf_uses_png_key(
    workflow: tuple[TestClient, MemoryStorage, ScriptedTextract, ScriptedModel],
) -> None:
    client, storage, _textract, _model = workflow
    response = client.post(
        "/api/v1/referrals",
        files={"file": ("png_named.pdf", PNG, "application/pdf")},
        data={"attestation": "true"},
    )
    assert response.status_code == 202
    _drain(client)
    assert any(key.endswith("/source.png") for key in storage.puts)


def test_storage_failure_stays_in_the_inbox(
    database: Database,
    material: object,
    jwk_client: JwkClient,
) -> None:
    _purge(database)

    class Boom(MemoryStorage):
        def put_bytes(self, key: str, body: bytes, content_type: str, sha256: bytes) -> object:
            raise StorageError("s3_unavailable")

    app = create_app(
        build_settings(s3_bucket_name="referrals-test-bucket"),
        jwk_client=jwk_client,
        engine=database.app_engine,
        storage=Boom(),
        textract=ScriptedTextract(),
        bedrock=ScriptedModel(sample_result()),
    )
    token = make_token(material)  # type: ignore[arg-type]
    with TestClient(app) as client:
        client.headers["Authorization"] = f"Bearer {token}"
        response = _upload(client, pdf_bytes(1), "boom.pdf")
        body = assert_problem(response, 503, "DEPENDENCY_UNAVAILABLE")
        listed = client.get("/api/v1/referrals")
    match = next(item for item in listed.json()["items"] if item["id"] == body["referral_id"])
    assert match["status"] == "failed"
    _purge(database)


def test_resume_does_not_start_twice(
    workflow: tuple[TestClient, MemoryStorage, ScriptedTextract, ScriptedModel],
) -> None:
    client, _storage, textract, _model = workflow
    textract.results.append(OcrCollect(kind="yield_ocr", poll_count=1))
    created = _upload(client, pdf_bytes(1), "slow.pdf")
    _drain(client)
    referral_id = created.json()["id"]
    assert client.get(f"/api/v1/referrals/{referral_id}/status").json()["status"] == "processing"
    history = len(client.get(f"/api/v1/referrals/{referral_id}").json()["history"])
    client.get(f"/api/v1/referrals/{referral_id}/status")
    _drain(client)
    assert len(textract.starts) == 1
    detail = client.get(f"/api/v1/referrals/{referral_id}")
    assert detail.json()["status"] == "needs_review"
    assert len(detail.json()["history"]) >= history


def test_retry_after_textract_failure(
    workflow: tuple[TestClient, MemoryStorage, ScriptedTextract, ScriptedModel],
) -> None:
    client, _storage, textract, _model = workflow
    textract.results.append(
        OcrCollect(
            kind="failed",
            error_code="OCR_FAILED",
            error_category="transient",
            retryable=True,
            poll_count=1,
        )
    )
    created = _upload(client, pdf_bytes(1), "retry.pdf")
    _drain(client)
    referral_id = created.json()["id"]
    failed = client.get(f"/api/v1/referrals/{referral_id}").json()
    assert failed["status"] == "failed"
    assert "retry" in failed["allowed_actions"]
    retried = client.post(
        f"/api/v1/referrals/{referral_id}/retry",
        json={"version": failed["version"]},
    )
    assert retried.status_code == 202
    _drain(client)
    assert len(textract.starts) == 2
    assert client.get(f"/api/v1/referrals/{referral_id}").json()["status"] == "needs_review"


def test_invalid_model_output_opens_review(
    workflow: tuple[TestClient, MemoryStorage, ScriptedTextract, ScriptedModel],
) -> None:
    client, _storage, _textract, model = workflow
    model.result = AiRunResult(
        status="failed",
        error_code="output_invalid",
        attempts=2,
        raw_output_text="{",
    )
    created = _upload(client, pdf_bytes(1), "bad-model.pdf")
    _drain(client)
    detail = client.get(f"/api/v1/referrals/{created.json()['id']}").json()
    assert detail["status"] == "needs_review"
    assert detail["current_run"]["error_code"] == "AI_OUTPUT_INVALID"
    assert detail["fields"][0]["suggested_value"] is None


def test_document_bytes_and_quiet_status_poll(
    workflow: tuple[TestClient, MemoryStorage, ScriptedTextract, ScriptedModel],
) -> None:
    client, _storage, _textract, _model = workflow
    payload = pdf_bytes(1)
    created = _upload(client, payload, "view.pdf")
    _drain(client)
    referral_id = created.json()["id"]
    opened = client.get(f"/api/v1/referrals/{referral_id}")
    before = len(opened.json()["history"])
    status = client.get(f"/api/v1/referrals/{referral_id}/status")
    assert status.status_code == 200
    assert len(client.get(f"/api/v1/referrals/{referral_id}").json()["history"]) == before + 1
    document = client.get(f"/api/v1/referrals/{referral_id}/document")
    assert document.status_code == 200
    assert document.content == payload
    assert document.headers["cache-control"] == "no-store"
    assert document.headers["content-disposition"].startswith('inline; filename="referral-')
    assert not any(name.lower().startswith("x-amz") for name in document.headers)


def test_approval_edit_and_terminal_conflict(
    workflow: tuple[TestClient, MemoryStorage, ScriptedTextract, ScriptedModel],
    database: Database,
) -> None:
    client, _storage, _textract, _model = workflow
    created = _upload(client, pdf_bytes(1), "review.pdf")
    _drain(client)
    referral_id = created.json()["id"]
    detail = client.get(f"/api/v1/referrals/{referral_id}").json()
    version = detail["version"]
    blocked = client.post(f"/api/v1/referrals/{referral_id}/approve", json=_approve(version, []))
    assert_problem(blocked, 422, "UNRESOLVED_BLOCKING_FLAGS")
    absent = {
        **_approve(version, []),
        "not_on_document": ["patient.full_name", "patient.date_of_birth"],
    }
    assert client.post(f"/api/v1/referrals/{referral_id}/approve", json=absent).status_code == 422
    patched = client.patch(
        f"/api/v1/referrals/{referral_id}",
        json={
            "version": version,
            "fields": [
                {"key": "patient.date_of_birth", "value": "1980-01-01"},
                {"key": "patient.phone", "value": "8565550142"},
                {"key": "referring_clinician.name", "value": "Alex Placeholder"},
                {"key": "referring_clinician.phone", "value": "8565550177"},
                {"key": "referral.reason", "value": "Palpitations"},
            ],
        },
    )
    assert patched.status_code == 200
    dob = next(
        field for field in patched.json()["fields"] if field["key"] == "patient.date_of_birth"
    )
    assert dob["confirmed_value"] == "1980-01-01"
    assert dob["suggested_value"] is None
    current = patched.json()["version"]
    assert_problem(
        client.post(f"/api/v1/referrals/{referral_id}/approve", json=_approve(version, [])),
        409,
        "VERSION_CONFLICT",
    )
    unknown = {
        "version": current,
        "destination_specialty": {"code": "oncology"},
        "sender_priority": "routine",
        "not_on_document": [],
        "acknowledgements": [],
    }
    assert_problem(
        client.post(f"/api/v1/referrals/{referral_id}/approve", json=unknown),
        422,
        "UNKNOWN_SPECIALTY",
    )
    approved = client.post(
        f"/api/v1/referrals/{referral_id}/approve",
        json=_approve(current, ["referring_clinician.fax"]),
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"
    with Session(database.app_engine) as session:
        referral = session.get(Referral, uuid.UUID(referral_id))
        events = list(
            session.scalars(
                select(AuditEvent).where(
                    AuditEvent.referral_id == uuid.UUID(referral_id),
                    AuditEvent.action == "approved",
                )
            )
        )
        field = session.scalar(
            select(ReferralField).where(
                ReferralField.referral_id == uuid.UUID(referral_id),
                ReferralField.field_key == "patient.date_of_birth",
            )
        )
    assert referral is not None and referral.destination_specialty_code == "cardiology"
    assert referral.specialty_decision == "accept_suggested"
    assert len(events) == 1
    assert field is not None and field.suggested_text is None
    assert_problem(
        client.post(
            f"/api/v1/referrals/{referral_id}/approve",
            json=_approve(current, []),
        ),
        409,
        "VERSION_CONFLICT",
    )
    assert_problem(
        client.patch(
            f"/api/v1/referrals/{referral_id}",
            json={
                "version": approved.json()["version"],
                "fields": [{"key": "referral.reason", "value": "Later"}],
            },
        ),
        409,
        "INVALID_TRANSITION",
    )


def test_reject_failed_referral(
    workflow: tuple[TestClient, MemoryStorage, ScriptedTextract, ScriptedModel],
) -> None:
    client, _storage, textract, _model = workflow
    textract.results.append(
        OcrCollect(
            kind="failed",
            error_code="OCR_FAILED",
            error_category="document",
            retryable=False,
            poll_count=1,
        )
    )
    created = _upload(client, pdf_bytes(1), "unreadable.pdf")
    _drain(client)
    referral_id = created.json()["id"]
    detail = client.get(f"/api/v1/referrals/{referral_id}").json()
    assert detail["status"] == "failed"
    missing = client.post(
        f"/api/v1/referrals/{referral_id}/reject",
        json={"version": detail["version"]},
    )
    assert_problem(missing, 422, "VALIDATION_ERROR")
    rejected = client.post(
        f"/api/v1/referrals/{referral_id}/reject",
        json={"version": detail["version"], "reason_code": "unreadable"},
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    assert rejected.json()["allowed_actions"] == []


def test_conflicting_name_blocks_approval(
    workflow: tuple[TestClient, MemoryStorage, ScriptedTextract, ScriptedModel],
) -> None:
    client, _storage, _textract, model = workflow
    extraction = sample_result().output
    assert extraction is not None
    extraction["patient"]["full_name"] = {
        "value": "",
        "status": "conflicting",
        "evidence": [{"line_id": "p1-l001", "quote": LINE}],
        "note": "Two spellings.",
    }
    model.result = AiRunResult(
        status="succeeded",
        attempts=1,
        stop_reason="end_turn",
        output=extraction,
        input_tokens=1,
        output_tokens=1,
        model_id="us.anthropic.claude-sonnet-5-5",
    )
    created = _upload(client, pdf_bytes(1), "conflict.pdf")
    _drain(client)
    referral_id = created.json()["id"]
    detail = client.get(f"/api/v1/referrals/{referral_id}").json()
    denied = client.post(
        f"/api/v1/referrals/{referral_id}/approve",
        json=_approve(detail["version"], []),
    )
    assert denied.status_code == 422
    fixed = client.patch(
        f"/api/v1/referrals/{referral_id}",
        json={
            "version": detail["version"],
            "fields": [{"key": "patient.full_name", "value": "Jordan Sample"}],
        },
    )
    assert fixed.status_code == 200


def test_specialty_mismatch_must_be_acknowledged(
    workflow: tuple[TestClient, MemoryStorage, ScriptedTextract, ScriptedModel],
) -> None:
    client, _storage, _textract, model = workflow
    extraction = sample_result().output
    assert extraction is not None
    extraction["referral"]["requested_specialty"] = {
        "label": "dermatology",
        "value": "Dermatology",
        "status": "explicit",
        "evidence": [{"line_id": "p1-l001", "quote": LINE}],
        "note": "",
    }
    model.result = AiRunResult(
        status="succeeded",
        attempts=1,
        stop_reason="end_turn",
        output=extraction,
        input_tokens=1,
        output_tokens=1,
        model_id="us.anthropic.claude-sonnet-5-5",
    )
    created = _upload(client, pdf_bytes(1), "mismatch.pdf")
    _drain(client)
    referral_id = created.json()["id"]
    detail = client.get(f"/api/v1/referrals/{referral_id}").json()
    mismatch = [flag for flag in detail["flags"] if flag["code"] == "specialty_mismatch"]
    assert len(mismatch) == 1
    patched = client.patch(
        f"/api/v1/referrals/{referral_id}",
        json={
            "version": detail["version"],
            "fields": [
                {"key": "patient.date_of_birth", "value": "1980-01-01"},
                {"key": "patient.phone", "value": "8565550142"},
                {"key": "referring_clinician.name", "value": "Alex Placeholder"},
                {"key": "referring_clinician.phone", "value": "8565550177"},
                {"key": "referral.reason", "value": "Palpitations"},
            ],
        },
    )
    assert patched.status_code == 200
    version = patched.json()["version"]
    blocked = assert_problem(
        client.post(
            f"/api/v1/referrals/{referral_id}/approve",
            json=_approve(version, ["referring_clinician.fax"]),
        ),
        422,
        "UNRESOLVED_BLOCKING_FLAGS",
    )
    errors = blocked["errors"]
    assert isinstance(errors, list)
    assert any("specialty_mismatch" in str(item.get("loc")) for item in errors)
    approved = client.post(
        f"/api/v1/referrals/{referral_id}/approve",
        json={
            **_approve(version, ["referring_clinician.fax"]),
            "acknowledgements": [
                {"flag_id": mismatch[0]["id"], "reason": "Staff chose the destination."}
            ],
        },
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"


def test_status_poll_writes_no_audit_of_its_own(
    workflow: tuple[TestClient, MemoryStorage, ScriptedTextract, ScriptedModel],
    database: Database,
) -> None:
    client, _storage, _textract, _model = workflow
    created = _upload(client, pdf_bytes(1), "poll.pdf")
    _drain(client)
    referral_id = uuid.UUID(created.json()["id"])
    client.get(f"/api/v1/referrals/{referral_id}")
    with Session(database.app_engine) as session:
        before = session.scalar(select(Referral.version).where(Referral.id == referral_id))
        viewed = list(
            session.scalars(
                select(AuditEvent).where(
                    AuditEvent.referral_id == referral_id,
                    AuditEvent.action == "referral_viewed",
                )
            )
        )
    client.get(f"/api/v1/referrals/{referral_id}/status")
    with Session(database.app_engine) as session:
        after = session.scalar(select(Referral.version).where(Referral.id == referral_id))
        viewed_after = list(
            session.scalars(
                select(AuditEvent).where(
                    AuditEvent.referral_id == referral_id,
                    AuditEvent.action == "referral_viewed",
                )
            )
        )
    assert after == before
    assert len(viewed_after) == len(viewed)


def test_matching_replay_is_stored() -> None:
    storage = _Exists()
    body = b"abc"
    digest = hashlib.sha256(body).digest()
    key = (
        "referrals/3f6c2a9e-8b1d-4c7a-9e2f-5d4b1a0c7e93/"
        "documents/a91d4e07-2c5b-4f8e-b3a6-0e7d9c1f2b58/source.pdf"
    )
    head = write_once(
        storage,
        key=key,
        body=body,
        content_type="application/pdf",
        sha256=digest,
        sha256_hex=digest.hex(),
    )
    assert head.bucket_key is True
    storage.sse = "AES256"
    with pytest.raises(StorageError) as caught:
        write_once(
            storage,
            key=key,
            body=body,
            content_type="application/pdf",
            sha256=digest,
            sha256_hex=digest.hex(),
        )
    assert caught.value.code == "s3_encryption_mismatch"
    _ = (STAFF_SUB, ProcessingRun, ValidationFlag)
