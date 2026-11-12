"""Schema acceptance tests T1–T4, T6, T7, and T15 on PostgreSQL 18."""

from __future__ import annotations

import importlib.util
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from psycopg.errors import (
    CheckViolation,
    ForeignKeyViolation,
    InsufficientPrivilege,
    UniqueViolation,
)
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session
from tests.conftest import STAFF_SUB, build_settings
from tests.integration.conftest import Database, migrate

from app.core.auth import JwkClient
from app.core.logging import configure_logging
from app.db.base import Base
from app.db.constants import AUDIT_ACTIONS, FIELD_KEYS, REFERRAL_STATUSES, TABLES
from app.db.models import (
    AiRun,
    AuditEvent,
    Document,
    OcrRun,
    ProcessingRun,
    Referral,
    ReferralField,
    ValidationFlag,
)
from app.main import create_app

BACKEND = Path(__file__).resolve().parents[2]
EXPECTED_INDEXES = frozenset(
    {
        "referrals_status_created_idx",
        "referrals_idempotency_key_uidx",
        "documents_referral_id_uidx",
        "documents_s3_key_uidx",
        "documents_sha256_idx",
        "processing_runs_referral_attempt_uidx",
        "processing_runs_one_active_uidx",
        "processing_runs_referral_idx",
        "processing_runs_ocr_run_id_idx",
        "ocr_runs_document_config_attempt_uidx",
        "ocr_runs_client_request_token_uidx",
        "ocr_runs_one_active_uidx",
        "ocr_runs_raw_output_s3_key_uidx",
        "ocr_runs_created_by_run_id_idx",
        "ai_runs_processing_run_id_uidx",
        "referral_fields_run_field_uidx",
        "referral_fields_referral_idx",
        "validation_flags_referral_code_field_uidx",
        "validation_flags_processing_run_id_idx",
        "audit_events_referral_occurred_idx",
        "audit_events_document_id_idx",
        "audit_events_run_id_idx",
    }
)
EXPECTED_TRIGGERS = frozenset(
    {
        "referral_fields_protect_suggestion",
        "audit_events_append_only",
    }
)


def _session(bind: object) -> Session:
    return Session(bind, expire_on_commit=False)  # type: ignore[arg-type]


def _tables(conn: object) -> set[str]:
    rows = conn.execute(  # type: ignore[attr-defined]
        text(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
            """
        )
    )
    return {row[0] for row in rows}


def _assert_schema(database: Database) -> None:
    with database.admin_engine.connect() as conn:
        version = int(conn.execute(text("SHOW server_version_num")).scalar_one())
        assert version // 10000 == 18
        assert set(TABLES) <= _tables(conn)
        indexes = {
            row[0]
            for row in conn.execute(
                text("SELECT indexname FROM pg_indexes WHERE schemaname = 'public'")
            )
        }
        assert EXPECTED_INDEXES <= indexes
        triggers = {
            row[0]
            for row in conn.execute(
                text(
                    """
                    SELECT tgname
                    FROM pg_trigger
                    WHERE NOT tgisinternal
                      AND tgrelid IN (
                        SELECT oid FROM pg_class
                        WHERE relnamespace = 'public'::regnamespace
                      )
                    """
                )
            )
        }
        assert triggers == EXPECTED_TRIGGERS
        key_checks = {
            row[0]
            for row in conn.execute(
                text(
                    """
                    SELECT conname
                    FROM pg_constraint
                    WHERE conname IN (
                      'documents_s3_key_ck',
                      'ocr_runs_raw_output_s3_key_ck'
                    )
                    """
                )
            )
        }
        assert key_checks == {
            "documents_s3_key_ck",
            "ocr_runs_raw_output_s3_key_ck",
        }
        functions = {
            row[0]
            for row in conn.execute(
                text(
                    """
                    SELECT p.proname
                    FROM pg_proc AS p
                    JOIN pg_namespace AS n ON n.oid = p.pronamespace
                    WHERE n.nspname = 'public'
                    """
                )
            )
        }
        assert {
            "demo_purge_referral",
            "referral_fields_protect_suggestion",
            "audit_events_append_only",
        } <= functions
        deferred = conn.execute(
            text(
                """
                SELECT condeferrable, condeferred
                FROM pg_constraint
                WHERE conname = 'referrals_current_run_id_fkey'
                """
            )
        ).one()
        assert deferred == (True, True)
        deletes = {
            row[0]: row[1]
            for row in conn.execute(
                text(
                    """
                    SELECT conname, confdeltype
                    FROM pg_constraint
                    WHERE conname IN (
                      'referrals_possible_duplicate_fkey',
                      'documents_possible_duplicate_fkey'
                    )
                    """
                )
            )
        }
        assert deletes == {
            "referrals_possible_duplicate_fkey": "n",
            "documents_possible_duplicate_fkey": "n",
        }
        for table in Base.metadata.tables.values():
            found = {
                row[0]: row[1] == "YES"
                for row in conn.execute(
                    text(
                        """
                        SELECT column_name, is_nullable
                        FROM information_schema.columns
                        WHERE table_schema = 'public' AND table_name = :table
                        """
                    ),
                    {"table": table.name},
                )
            }
            expected = {column.name: column.nullable for column in table.columns}
            assert found == expected
        for key in FIELD_KEYS:
            definition = conn.execute(
                text(
                    """
                    SELECT pg_get_constraintdef(oid)
                    FROM pg_constraint
                    WHERE conname = 'referral_fields_field_key_ck'
                    """
                )
            ).scalar_one()
            assert f"'{key}'" in definition
        action_def = conn.execute(
            text(
                """
                SELECT pg_get_constraintdef(oid)
                FROM pg_constraint
                WHERE conname = 'audit_events_action_ck'
                """
            )
        ).scalar_one()
        for action in AUDIT_ACTIONS:
            assert f"'{action}'" in action_def
        status_def = conn.execute(
            text(
                """
                SELECT pg_get_constraintdef(oid)
                FROM pg_constraint
                WHERE conname = 'referrals_status_ck'
                """
            )
        ).scalar_one()
        for status in REFERRAL_STATUSES:
            assert f"'{status}'" in status_def
        assert conn.execute(
            text("SELECT has_table_privilege('intake_app', 'public.audit_events', 'SELECT')")
        ).scalar_one()
        assert conn.execute(
            text("SELECT has_table_privilege('intake_app', 'public.audit_events', 'INSERT')")
        ).scalar_one()
        assert (
            conn.execute(
                text("SELECT has_table_privilege('intake_app', 'public.audit_events', 'UPDATE')")
            ).scalar_one()
            is False
        )
        assert (
            conn.execute(
                text("SELECT has_table_privilege('intake_app', 'public.audit_events', 'DELETE')")
            ).scalar_one()
            is False
        )
        for table in TABLES:
            assert (
                conn.execute(
                    text("SELECT has_table_privilege('intake_app', :table, 'DELETE')"),
                    {"table": f"public.{table}"},
                ).scalar_one()
                is False
            )
            assert conn.execute(
                text("SELECT has_table_privilege('intake_app', :table, 'SELECT')"),
                {"table": f"public.{table}"},
            ).scalar_one()
        assert conn.execute(
            text("SELECT has_table_privilege('intake_app', 'public.referrals', 'UPDATE')")
        ).scalar_one()
        assert (
            conn.execute(
                text("SELECT has_table_privilege('intake_app', 'public.referrals', 'TRUNCATE')")
            ).scalar_one()
            is False
        )
        assert (
            conn.execute(
                text("SELECT has_schema_privilege('intake_app', 'public', 'CREATE')")
            ).scalar_one()
            is False
        )
        assert conn.execute(
            text("SELECT has_schema_privilege('intake_app', 'public', 'USAGE')")
        ).scalar_one()
        assert (
            conn.execute(
                text(
                    "SELECT has_function_privilege("
                    "'intake_app', 'public.demo_purge_referral(uuid)', 'EXECUTE')"
                )
            ).scalar_one()
            is False
        )
        with database.app_engine.connect() as app_conn:
            assert app_conn.execute(text("SELECT current_user")).scalar_one() == "intake_app"
            ssl_on = app_conn.execute(
                text("SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()")
            ).scalar_one()
            assert ssl_on is False


def test_frozen_field_keys_match_the_revision() -> None:
    path = BACKEND / "migrations" / "versions" / "0001_initial.py"
    spec = importlib.util.spec_from_file_location("initial_revision", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert tuple(module._FIELD_KEYS) == FIELD_KEYS


def test_t1_upgrade_downgrade_and_grants(database: Database) -> None:
    _assert_schema(database)
    database.admin_engine.dispose()
    database.app_engine.dispose()
    try:
        migrate(
            "intake_test",
            "base",
            user=database.user,
            port=database.port,
            password=database.password,
        )
        with database.admin_engine.connect() as conn:
            present = _tables(conn)
            for table in TABLES:
                assert table not in present
    finally:
        migrate(
            "intake_test",
            "head",
            user=database.user,
            port=database.port,
            password=database.password,
        )
    _assert_schema(database)


def _referral(session: Session) -> Referral:
    referral = Referral(
        id=uuid.uuid4(),
        status="uploaded",
        version=1,
        created_by=STAFF_SUB,
    )
    session.add(referral)
    session.flush()
    return referral


def _run(session: Session, referral: Referral, *, attempt: int = 1) -> ProcessingRun:
    run = ProcessingRun(
        id=uuid.uuid4(),
        referral_id=referral.id,
        attempt=attempt,
        status="queued",
        trigger_kind="upload",
        request_id=uuid.uuid4(),
    )
    session.add(run)
    session.flush()
    referral.current_run_id = run.id
    session.flush()
    return run


def _field(session: Session, referral: Referral, run: ProcessingRun) -> ReferralField:
    field = ReferralField(
        id=uuid.uuid4(),
        processing_run_id=run.id,
        referral_id=referral.id,
        field_key="patient.date_of_birth",
        suggested_text="1968-04-12",
        ai_status="normalized",
    )
    session.add(field)
    session.flush()
    return field


def test_t2_app_cannot_update_or_delete_audit_events(database: Database) -> None:
    with _session(database.app_engine) as session:
        referral = _referral(session)
        event = AuditEvent(
            audit_event_id=uuid.uuid4(),
            actor_type="staff",
            actor_id=STAFF_SUB,
            action="uploaded",
            referral_id=referral.id,
            request_id=uuid.uuid4(),
        )
        session.add(event)
        session.commit()
        event_id = event.audit_event_id

    with _session(database.app_engine) as session:
        row = session.get(AuditEvent, event_id)
        assert row is not None
        row.reason = "not allowed"
        with pytest.raises(DBAPIError) as exc_info:
            session.commit()
        assert isinstance(exc_info.value.orig, InsufficientPrivilege)

    with _session(database.app_engine) as session:
        row = session.get(AuditEvent, event_id)
        assert row is not None
        session.delete(row)
        with pytest.raises(DBAPIError) as exc_info:
            session.commit()
        assert isinstance(exc_info.value.orig, InsufficientPrivilege)


def test_t3_app_cannot_delete_a_referral(database: Database) -> None:
    with _session(database.app_engine) as session:
        referral = _referral(session)
        session.commit()
        referral_id = referral.id

    with _session(database.app_engine) as session:
        row = session.get(Referral, referral_id)
        assert row is not None
        session.delete(row)
        with pytest.raises(DBAPIError) as exc_info:
            session.commit()
        assert isinstance(exc_info.value.orig, InsufficientPrivilege)


def test_t4_suggestion_trigger_rejects_suggested_text(database: Database) -> None:
    with _session(database.app_engine) as session:
        referral = _referral(session)
        run = _run(session, referral)
        field = _field(session, referral, run)
        session.commit()
        field_id = field.id

    with _session(database.app_engine) as session:
        row = session.get(ReferralField, field_id)
        assert row is not None
        row.confirmed_text = "1968-04-13"
        row.confirmed_at = datetime.now(UTC)
        row.confirmed_by = STAFF_SUB
        session.commit()
        assert row.suggested_text == "1968-04-12"
        assert row.confirmed_text == "1968-04-13"

    with _session(database.app_engine) as session:
        row = session.get(ReferralField, field_id)
        assert row is not None
        row.suggested_text = "1999-01-01"
        with pytest.raises(IntegrityError) as exc_info:
            session.commit()
        assert isinstance(exc_info.value.orig, CheckViolation)
        assert "suggested values are frozen" in str(exc_info.value.orig)


def test_t6_composite_foreign_key_rejects_a_mismatched_referral(database: Database) -> None:
    with _session(database.app_engine) as session:
        first = _referral(session)
        second = _referral(session)
        run = _run(session, first)
        session.add(
            ReferralField(
                id=uuid.uuid4(),
                processing_run_id=run.id,
                referral_id=second.id,
                field_key="patient.full_name",
                ai_status="explicit",
            )
        )
        with pytest.raises(IntegrityError) as exc_info:
            session.flush()
        assert isinstance(exc_info.value.orig, ForeignKeyViolation)


def test_t7_one_active_processing_run(database: Database) -> None:
    with _session(database.app_engine) as session:
        referral = _referral(session)
        _run(session, referral, attempt=1)
        session.add(
            ProcessingRun(
                id=uuid.uuid4(),
                referral_id=referral.id,
                attempt=2,
                status="queued",
                trigger_kind="retry",
                request_id=uuid.uuid4(),
            )
        )
        with pytest.raises(IntegrityError) as exc_info:
            session.flush()
        assert isinstance(exc_info.value.orig, UniqueViolation)


def test_object_keys_match_the_referral_folder(database: Database) -> None:
    with _session(database.app_engine) as session:
        referral = _referral(session)
        document_id = uuid.uuid4()
        session.add(
            Document(
                id=document_id,
                referral_id=referral.id,
                s3_bucket="synthetic-bucket",
                s3_key=f"referrals/{referral.id}/documents/{document_id}/source.pdf",
                media_type="application/pdf",
                original_filename="synthetic.pdf",
                storage_status="storing",
                uploaded_by=STAFF_SUB,
            )
        )
        with pytest.raises(IntegrityError) as exc_info:
            session.flush()
        assert isinstance(exc_info.value.orig, CheckViolation)

    with _session(database.app_engine) as session:
        referral = _referral(session)
        document = Document(
            id=uuid.uuid4(),
            referral_id=referral.id,
            s3_bucket="synthetic-bucket",
            s3_key=f"referrals/{uuid.uuid4()}/source.pdf",
            media_type="application/pdf",
            original_filename="synthetic.pdf",
            storage_status="storing",
            uploaded_by=STAFF_SUB,
        )
        session.add(document)
        with pytest.raises(IntegrityError) as exc_info:
            session.flush()
        assert isinstance(exc_info.value.orig, CheckViolation)

    with _session(database.app_engine) as session:
        referral = _referral(session)
        document = Document(
            id=uuid.uuid4(),
            referral_id=referral.id,
            s3_bucket="synthetic-bucket",
            s3_key=f"referrals/{referral.id}/source.pdf",
            media_type="application/pdf",
            original_filename="synthetic.pdf",
            storage_status="storing",
            uploaded_by=STAFF_SUB,
        )
        session.add(document)
        session.flush()
        run = _run(session, referral)
        first_id = uuid.uuid4()
        second_id = uuid.uuid4()
        session.add(
            OcrRun(
                id=first_id,
                document_id=document.id,
                referral_id=referral.id,
                created_by_run_id=run.id,
                attempt=1,
                config_version="ocr-v1",
                feature_types=["LAYOUT", "FORMS", "TABLES"],
                client_request_token=f"ocr-{document.id.hex}-c1-a1",
                status="failed",
                raw_output_s3_key=f"referrals/{referral.id}/ocr/{first_id}.json.gz",
            )
        )
        second = OcrRun(
            id=second_id,
            document_id=document.id,
            referral_id=referral.id,
            created_by_run_id=run.id,
            attempt=2,
            config_version="ocr-v1",
            feature_types=["LAYOUT", "FORMS", "TABLES"],
            client_request_token=f"ocr-{document.id.hex}-c1-a2",
            status="failed",
            raw_output_s3_key=f"referrals/{referral.id}/ocr/{second_id}.json.gz",
        )
        session.add(second)
        session.flush()
        second.raw_output_s3_key = f"referrals/{referral.id}/ocr/{uuid.uuid4()}.json.gz"
        with pytest.raises(IntegrityError) as exc_info:
            session.flush()
        assert isinstance(exc_info.value.orig, CheckViolation)


def test_t15_purge_removes_one_referral_and_app_cannot_call_it(database: Database) -> None:
    with database.app_engine.connect() as conn:
        with pytest.raises(DBAPIError) as exc_info:
            conn.execute(
                text("SELECT demo_purge_referral(:referral_id)"),
                {"referral_id": uuid.uuid4()},
            )
        assert isinstance(exc_info.value.orig, InsufficientPrivilege)
        conn.rollback()

    with _session(database.app_engine) as session:
        keeper = _referral(session)
        session.commit()
        keeper_id = keeper.id

    with _session(database.app_engine) as session:
        referral = _referral(session)
        document_id = uuid.uuid4()
        document = Document(
            id=document_id,
            referral_id=referral.id,
            s3_bucket="synthetic-bucket",
            s3_key=f"referrals/{referral.id}/source.pdf",
            media_type="application/pdf",
            original_filename="synthetic.pdf",
            storage_status="storing",
            uploaded_by=STAFF_SUB,
        )
        session.add(document)
        session.flush()
        run = _run(session, referral)
        ocr = OcrRun(
            id=uuid.uuid4(),
            document_id=document.id,
            referral_id=referral.id,
            created_by_run_id=run.id,
            attempt=1,
            config_version="ocr-v1",
            feature_types=["LAYOUT", "FORMS", "TABLES"],
            client_request_token=f"ocr-{document.id.hex}-c1-a1",
            status="queued",
        )
        session.add(ocr)
        session.flush()
        run.ocr_run_id = ocr.id
        session.add(
            AiRun(
                id=uuid.uuid4(),
                processing_run_id=run.id,
                referral_id=referral.id,
                status="queued",
                model_id="us.anthropic.claude-sonnet-5-5",
                prompt_version="referral-extract-v1",
                schema_version="referral_extraction_v1",
                invocation_count=1,
            )
        )
        _field(session, referral, run)
        session.add(
            ValidationFlag(
                id=uuid.uuid4(),
                referral_id=referral.id,
                processing_run_id=run.id,
                code="DUPLICATE_DOCUMENT",
                severity="warning",
                message="Synthetic duplicate warning",
                status="open",
            )
        )
        event = AuditEvent(
            audit_event_id=uuid.uuid4(),
            actor_type="staff",
            actor_id=STAFF_SUB,
            action="uploaded",
            referral_id=referral.id,
            document_id=document.id,
            run_id=run.id,
            request_id=uuid.uuid4(),
        )
        session.add(event)
        session.commit()
        referral_id = referral.id
        event_id = event.audit_event_id

    with _session(database.admin_engine) as session:
        row = session.get(AuditEvent, event_id)
        assert row is not None
        session.delete(row)
        with pytest.raises(IntegrityError) as exc_info:
            session.commit()
        assert isinstance(exc_info.value.orig, CheckViolation)
        assert "append-only" in str(exc_info.value.orig)

    with database.admin_engine.begin() as conn:
        conn.execute(
            text("SELECT demo_purge_referral(:referral_id)"),
            {"referral_id": referral_id},
        )

    with database.admin_engine.connect() as conn:
        assert _rows_for(conn, referral_id) == 0
        assert _rows_for(conn, keeper_id) == 1


def test_app_starts_with_the_app_password_only(
    database: Database,
    jwk_client: JwkClient,
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging(
        build_settings(
            app_env="local",
            db_port=database.port,
            db_name="intake_test",
            db_password=database.password,
        )
    )
    app = create_app(
        build_settings(
            app_env="local",
            db_host="127.0.0.1",
            db_port=database.port,
            db_name="intake_test",
            db_user="intake_app",
            db_password=database.password,
            db_sslmode="disable",
        ),
        jwk_client=jwk_client,
    )
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        response = client.get("/healthz")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
    captured = capsys.readouterr()
    assert (database.password in captured.out or database.password in captured.err) is False
    body = json.loads(next(line for line in captured.out.splitlines() if '"db.connected"' in line))
    assert body["event"] == "db.connected"
    assert body["ssl"] is False
    assert "intake_admin" not in captured.out


def test_env_file_has_no_admin_password() -> None:
    text_value = (BACKEND / ".env").read_text(encoding="utf-8")
    assert "DB_ADMIN_PASSWORD" not in text_value
    assert "DB_APP_PASSWORD" not in text_value
    assert "intake_admin" not in text_value
    for forbidden in (
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
    ):
        assert forbidden not in text_value


def _rows_for(conn: object, referral_id: uuid.UUID) -> int:
    total = int(
        conn.execute(  # type: ignore[attr-defined]
            text("SELECT count(*) FROM referrals WHERE id = :referral_id"),
            {"referral_id": referral_id},
        ).scalar_one()
    )
    for table in TABLES:
        if table == "referrals":
            continue
        total += int(
            conn.execute(  # type: ignore[attr-defined]
                text(f"SELECT count(*) FROM {table} WHERE referral_id = :referral_id"),
                {"referral_id": referral_id},
            ).scalar_one()
        )
    return total
