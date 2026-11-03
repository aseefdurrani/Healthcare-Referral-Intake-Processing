"""Initial intake schema. Do not edit this revision after it has been applied.

Frozen copies of the closed lists live in this file. A later change belongs in
a new revision. ``DB_APP_PASSWORD`` comes from the operator shell, not ``.env``.
"""

from __future__ import annotations

import os
import re

from alembic import op
from psycopg import sql as psql
from sqlalchemy import text

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_FIELD_KEYS = (
    "patient.full_name",
    "patient.date_of_birth",
    "patient.phone",
    "patient.email",
    "patient.address",
    "patient.identifiers",
    "referring_clinician.name",
    "referring_clinician.organization",
    "referring_clinician.phone",
    "referring_clinician.fax",
    "referring_clinician.npi",
    "referral.referral_date",
    "referral.reason",
    "referral.sender_priority",
    "referral.requested_specialty",
    "referral.diagnosis_codes",
)


def _sql_list(values: tuple[str, ...]) -> str:
    if any("'" in value for value in values):
        raise RuntimeError("A frozen list value contains a quote.")
    return ", ".join(f"'{value}'" for value in values)


_FIELDS = _sql_list(_FIELD_KEYS)

_UPGRADE_SQL = (
    """
    CREATE TABLE referrals (
      id uuid PRIMARY KEY,
      status text NOT NULL,
      version integer NOT NULL,
      current_run_id uuid,
      idempotency_key uuid,
      possible_duplicate_of_referral_id uuid,
      specialty_decision text,
      destination_specialty_code text,
      destination_specialty_other text,
      rejection_reason_code text,
      rejection_note text,
      created_by text NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL,
      CONSTRAINT referrals_status_ck CHECK (
        status IN (
          'uploaded', 'processing', 'needs_review',
          'failed', 'approved', 'rejected'
        )
      ),
      CONSTRAINT referrals_version_ck CHECK (version >= 1),
      CONSTRAINT referrals_not_self_duplicate_ck CHECK (
        possible_duplicate_of_referral_id IS NULL
        OR possible_duplicate_of_referral_id <> id
      ),
      CONSTRAINT referrals_specialty_decision_ck CHECK (
        specialty_decision IS NULL
        OR specialty_decision IN ('accept_suggested', 'override')
      ),
      CONSTRAINT referrals_destination_ck CHECK (
        (
          destination_specialty_code IS NULL
          AND destination_specialty_other IS NULL
        )
        OR (
          destination_specialty_code = 'other'
          AND destination_specialty_other IS NOT NULL
          AND btrim(destination_specialty_other) <> ''
          AND char_length(destination_specialty_other) <= 200
        )
        OR (
          destination_specialty_code IN (
            'cardiology', 'dermatology', 'gastroenterology',
            'neurology', 'orthopedics', 'pulmonology'
          )
          AND destination_specialty_other IS NULL
        )
      ),
      CONSTRAINT referrals_rejection_ck CHECK (
        (
          (
            rejection_reason_code IS NULL
            OR rejection_reason_code IN (
              'not_a_referral', 'unreadable', 'duplicate', 'incomplete'
            )
          )
          AND (
            rejection_note IS NULL OR char_length(rejection_note) <= 1000
          )
        )
        OR (
          rejection_reason_code = 'other'
          AND rejection_note IS NOT NULL
          AND btrim(rejection_note) <> ''
          AND char_length(rejection_note) <= 1000
        )
      ),
      CONSTRAINT referrals_approved_ck CHECK (
        status <> 'approved'
        OR (
          specialty_decision IS NOT NULL
          AND destination_specialty_code IS NOT NULL
          AND rejection_reason_code IS NULL
        )
      ),
      CONSTRAINT referrals_rejected_ck CHECK (
        status <> 'rejected'
        OR (
          rejection_reason_code IS NOT NULL
          AND specialty_decision IS NULL
        )
      ),
      CONSTRAINT referrals_possible_duplicate_fkey
        FOREIGN KEY (possible_duplicate_of_referral_id)
        REFERENCES referrals (id)
        ON DELETE SET NULL
    )
    """,
    """
    CREATE TABLE documents (
      id uuid PRIMARY KEY,
      referral_id uuid NOT NULL,
      s3_bucket text NOT NULL,
      s3_key text NOT NULL,
      s3_version_id text,
      media_type text NOT NULL,
      size_bytes bigint,
      page_count integer,
      sha256_hex char(64),
      s3_etag text,
      sse_algorithm text,
      original_filename text NOT NULL,
      storage_status text NOT NULL,
      storage_error_code text,
      possible_duplicate_of_document_id uuid,
      uploaded_by text NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now(),
      stored_at timestamptz,
      updated_at timestamptz NOT NULL,
      CONSTRAINT documents_id_referral_id_key UNIQUE (id, referral_id),
      CONSTRAINT documents_media_type_ck CHECK (
        media_type IN ('application/pdf', 'image/png', 'image/jpeg')
      ),
      CONSTRAINT documents_filename_ck CHECK (
        char_length(original_filename) BETWEEN 1 AND 255
      ),
      CONSTRAINT documents_storage_status_ck CHECK (
        storage_status IN ('storing', 'stored', 'storage_failed')
      ),
      CONSTRAINT documents_size_ck CHECK (
        (storage_status <> 'storing' OR size_bytes IS NULL)
        AND (
          size_bytes IS NULL
          OR (size_bytes >= 1 AND size_bytes <= 10000000)
        )
      ),
      CONSTRAINT documents_page_count_ck CHECK (
        (storage_status <> 'storing' OR page_count IS NULL)
        AND (page_count IS NULL OR (page_count >= 1 AND page_count <= 10))
      ),
      CONSTRAINT documents_sha256_ck CHECK (
        sha256_hex IS NULL OR sha256_hex ~ '^[0-9a-f]{64}$'
      ),
      CONSTRAINT documents_stored_ck CHECK (
        storage_status <> 'stored'
        OR (
          size_bytes IS NOT NULL
          AND page_count IS NOT NULL
          AND sha256_hex IS NOT NULL
          AND s3_etag IS NOT NULL
          AND sse_algorithm IS NOT NULL
          AND stored_at IS NOT NULL
        )
      ),
      CONSTRAINT documents_referral_id_fkey
        FOREIGN KEY (referral_id) REFERENCES referrals (id) ON DELETE RESTRICT,
      CONSTRAINT documents_possible_duplicate_fkey
        FOREIGN KEY (possible_duplicate_of_document_id)
        REFERENCES documents (id)
        ON DELETE SET NULL
    )
    """,
    """
    CREATE TABLE processing_runs (
      id uuid PRIMARY KEY,
      referral_id uuid NOT NULL,
      attempt integer NOT NULL,
      status text NOT NULL,
      trigger_kind text NOT NULL,
      ocr_run_id uuid,
      request_id uuid NOT NULL,
      error_code text,
      retryable boolean,
      failure_stage text,
      failure_detail text,
      textract_pages integer,
      textract_job_id text,
      bedrock_model_id text,
      input_tokens integer,
      output_tokens integer,
      estimated_cost_usd numeric(12, 6),
      pricing_rates_as_of date,
      aws_request_id text,
      ocr_ms integer,
      ai_ms integer,
      started_at timestamptz,
      finished_at timestamptz,
      created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL,
      CONSTRAINT processing_runs_id_referral_id_key UNIQUE (id, referral_id),
      CONSTRAINT processing_runs_attempt_ck CHECK (attempt >= 1),
      CONSTRAINT processing_runs_status_ck CHECK (
        status IN ('queued', 'running', 'succeeded', 'failed')
      ),
      CONSTRAINT processing_runs_trigger_ck CHECK (
        trigger_kind IN ('upload', 'retry')
      ),
      CONSTRAINT processing_runs_failure_stage_ck CHECK (
        failure_stage IS NULL OR failure_stage IN ('ocr', 'ai', 'other')
      ),
      CONSTRAINT processing_runs_failure_detail_ck CHECK (
        failure_detail IS NULL OR failure_detail IN (
          'upload', 'storage', 'ocr', 'ai',
          'validation', 'persistence', 'timeout', 'unknown'
        )
      ),
      CONSTRAINT processing_runs_textract_pages_ck CHECK (
        textract_pages IS NULL OR textract_pages >= 0
      ),
      CONSTRAINT processing_runs_input_tokens_ck CHECK (
        input_tokens IS NULL OR input_tokens >= 0
      ),
      CONSTRAINT processing_runs_output_tokens_ck CHECK (
        output_tokens IS NULL OR output_tokens >= 0
      ),
      CONSTRAINT processing_runs_cost_ck CHECK (
        estimated_cost_usd IS NULL OR estimated_cost_usd >= 0
      ),
      CONSTRAINT processing_runs_ocr_ms_ck CHECK (ocr_ms IS NULL OR ocr_ms >= 0),
      CONSTRAINT processing_runs_ai_ms_ck CHECK (ai_ms IS NULL OR ai_ms >= 0),
      CONSTRAINT processing_runs_referral_id_fkey
        FOREIGN KEY (referral_id) REFERENCES referrals (id) ON DELETE RESTRICT
    )
    """,
    """
    CREATE TABLE ocr_runs (
      id uuid PRIMARY KEY,
      document_id uuid NOT NULL,
      referral_id uuid NOT NULL,
      created_by_run_id uuid NOT NULL,
      attempt integer NOT NULL,
      config_version text NOT NULL,
      feature_types text[] NOT NULL,
      client_request_token text NOT NULL,
      textract_job_id text,
      status text NOT NULL,
      error_category text,
      error_code text,
      status_message text,
      preflight_page_count integer,
      textract_page_count integer,
      pages_billed_estimate integer,
      model_version text,
      normalizer_version text,
      raw_output_s3_key text,
      raw_output_sha256_hex char(64),
      raw_output_size_bytes bigint,
      normalized jsonb,
      mean_word_confidence numeric(5, 2),
      flag_codes text[] NOT NULL DEFAULT '{}'::text[],
      poll_count integer NOT NULL DEFAULT 0,
      started_at timestamptz,
      completed_at timestamptz,
      created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL,
      CONSTRAINT ocr_runs_attempt_ck CHECK (attempt >= 1),
      CONSTRAINT ocr_runs_status_ck CHECK (
        status IN ('queued', 'running', 'succeeded', 'failed', 'timed_out')
      ),
      CONSTRAINT ocr_runs_status_message_ck CHECK (
        status_message IS NULL OR char_length(status_message) <= 500
      ),
      CONSTRAINT ocr_runs_pages_billed_ck CHECK (
        pages_billed_estimate IS NULL OR pages_billed_estimate >= 0
      ),
      CONSTRAINT ocr_runs_raw_size_ck CHECK (
        raw_output_size_bytes IS NULL OR raw_output_size_bytes >= 0
      ),
      CONSTRAINT ocr_runs_sha256_ck CHECK (
        raw_output_sha256_hex IS NULL
        OR raw_output_sha256_hex ~ '^[0-9a-f]{64}$'
      ),
      CONSTRAINT ocr_runs_confidence_ck CHECK (
        mean_word_confidence IS NULL
        OR (mean_word_confidence >= 0 AND mean_word_confidence <= 100)
      ),
      CONSTRAINT ocr_runs_poll_count_ck CHECK (poll_count >= 0),
      CONSTRAINT ocr_runs_succeeded_ck CHECK (
        status <> 'succeeded' OR normalized IS NOT NULL
      ),
      CONSTRAINT ocr_runs_document_referral_fkey
        FOREIGN KEY (document_id, referral_id)
        REFERENCES documents (id, referral_id)
        ON DELETE RESTRICT,
      CONSTRAINT ocr_runs_created_by_run_fkey
        FOREIGN KEY (created_by_run_id, referral_id)
        REFERENCES processing_runs (id, referral_id)
        ON DELETE RESTRICT
    )
    """,
    """
    CREATE TABLE ai_runs (
      id uuid PRIMARY KEY,
      processing_run_id uuid NOT NULL,
      referral_id uuid NOT NULL,
      status text NOT NULL,
      model_id text NOT NULL,
      prompt_version text NOT NULL,
      schema_version text NOT NULL,
      invocation_count smallint NOT NULL,
      input_tokens integer,
      output_tokens integer,
      latency_ms integer,
      estimated_cost_usd numeric(12, 6),
      stop_reason text,
      validated_output jsonb,
      raw_output_text text,
      specialty_label text,
      other_specialty_name text,
      relation_to_request text,
      rationale text,
      candidates jsonb NOT NULL DEFAULT '[]'::jsonb,
      error_code text,
      started_at timestamptz,
      completed_at timestamptz,
      created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL,
      CONSTRAINT ai_runs_status_ck CHECK (
        status IN ('queued', 'running', 'succeeded', 'failed')
      ),
      CONSTRAINT ai_runs_invocation_ck CHECK (invocation_count IN (1, 2)),
      CONSTRAINT ai_runs_input_tokens_ck CHECK (
        input_tokens IS NULL OR input_tokens >= 0
      ),
      CONSTRAINT ai_runs_output_tokens_ck CHECK (
        output_tokens IS NULL OR output_tokens >= 0
      ),
      CONSTRAINT ai_runs_latency_ck CHECK (latency_ms IS NULL OR latency_ms >= 0),
      CONSTRAINT ai_runs_cost_ck CHECK (
        estimated_cost_usd IS NULL OR estimated_cost_usd >= 0
      ),
      CONSTRAINT ai_runs_specialty_ck CHECK (
        specialty_label IS NULL OR specialty_label IN (
          'cardiology', 'dermatology', 'gastroenterology', 'neurology',
          'orthopedics', 'pulmonology', 'other_specialty', 'uncertain'
        )
      ),
      CONSTRAINT ai_runs_succeeded_ck CHECK (
        status <> 'succeeded'
        OR (
          validated_output IS NOT NULL
          AND specialty_label IS NOT NULL
          AND raw_output_text IS NULL
        )
      ),
      CONSTRAINT ai_runs_raw_output_ck CHECK (
        raw_output_text IS NULL OR status = 'failed'
      ),
      CONSTRAINT ai_runs_processing_run_fkey
        FOREIGN KEY (processing_run_id, referral_id)
        REFERENCES processing_runs (id, referral_id)
        ON DELETE RESTRICT
    )
    """,
    f"""
    CREATE TABLE referral_fields (
      id uuid PRIMARY KEY,
      processing_run_id uuid NOT NULL,
      referral_id uuid NOT NULL,
      field_key text NOT NULL,
      suggested_text text,
      suggested_json jsonb,
      ai_status text NOT NULL,
      evidence jsonb NOT NULL DEFAULT '[]'::jsonb,
      note text,
      confirmed_text text,
      confirmed_json jsonb,
      confirmed_absent boolean NOT NULL DEFAULT false,
      confirmed_at timestamptz,
      confirmed_by text,
      created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL,
      CONSTRAINT referral_fields_field_key_ck CHECK (
        field_key IN ({_FIELDS})
      ),
      CONSTRAINT referral_fields_ai_status_ck CHECK (
        ai_status IN (
          'explicit', 'normalized', 'not_found', 'ambiguous', 'conflicting'
        )
      ),
      CONSTRAINT referral_fields_confirmed_absent_ck CHECK (
        confirmed_absent = false
        OR (confirmed_text IS NULL AND confirmed_json IS NULL)
      ),
      CONSTRAINT referral_fields_confirmation_ck CHECK (
        (
          confirmed_text IS NULL
          AND confirmed_json IS NULL
          AND confirmed_absent = false
          AND confirmed_at IS NULL
          AND confirmed_by IS NULL
        )
        OR (
          confirmed_at IS NOT NULL
          AND confirmed_by IS NOT NULL
          AND (
            confirmed_text IS NOT NULL
            OR confirmed_json IS NOT NULL
            OR confirmed_absent = true
          )
        )
      ),
      CONSTRAINT referral_fields_processing_run_fkey
        FOREIGN KEY (processing_run_id, referral_id)
        REFERENCES processing_runs (id, referral_id)
        ON DELETE RESTRICT
    )
    """,
    f"""
    CREATE TABLE validation_flags (
      id uuid PRIMARY KEY,
      referral_id uuid NOT NULL,
      processing_run_id uuid,
      code text NOT NULL,
      severity text NOT NULL,
      field_key text,
      message text NOT NULL,
      status text NOT NULL,
      acknowledgement_reason text,
      created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL,
      acknowledged_at timestamptz,
      CONSTRAINT validation_flags_severity_ck CHECK (
        severity IN ('blocking', 'warning')
      ),
      CONSTRAINT validation_flags_status_ck CHECK (
        status IN ('open', 'resolved', 'acknowledged')
      ),
      CONSTRAINT validation_flags_field_key_ck CHECK (
        field_key IS NULL OR field_key IN ({_FIELDS})
      ),
      CONSTRAINT validation_flags_acknowledged_ck CHECK (
        status <> 'acknowledged'
        OR (
          acknowledgement_reason IS NOT NULL
          AND char_length(acknowledgement_reason) BETWEEN 3 AND 500
          AND acknowledged_at IS NOT NULL
        )
      ),
      CONSTRAINT validation_flags_referral_id_fkey
        FOREIGN KEY (referral_id)
        REFERENCES referrals (id)
        ON DELETE RESTRICT,
      CONSTRAINT validation_flags_processing_run_id_fkey
        FOREIGN KEY (processing_run_id)
        REFERENCES processing_runs (id)
        ON DELETE RESTRICT
    )
    """,
    """
    CREATE TABLE audit_events (
      audit_event_id uuid PRIMARY KEY,
      occurred_at timestamptz NOT NULL DEFAULT now(),
      actor_type text NOT NULL,
      actor_id text,
      action text NOT NULL,
      referral_id uuid NOT NULL,
      document_id uuid,
      run_id uuid,
      field_name text,
      old_value jsonb,
      new_value jsonb,
      from_status text,
      to_status text,
      reason text,
      request_id uuid NOT NULL,
      CONSTRAINT audit_events_actor_type_ck CHECK (
        actor_type IN ('staff', 'system')
      ),
      CONSTRAINT audit_events_action_ck CHECK (
        action IN (
          'uploaded', 'referral_viewed', 'document_viewed', 'field_corrected',
          'specialty_changed', 'approved', 'rejected', 'reprocess_requested',
          'processing_started', 'processing_failed', 'ready_for_review'
        )
      ),
      CONSTRAINT audit_events_actor_ck CHECK (
        (actor_type = 'staff' AND actor_id IS NOT NULL)
        OR (actor_type = 'system' AND actor_id IS NULL)
      ),
      CONSTRAINT audit_events_field_corrected_ck CHECK (
        action <> 'field_corrected' OR field_name IS NOT NULL
      ),
      CONSTRAINT audit_events_values_ck CHECK (
        (old_value IS NULL AND new_value IS NULL)
        OR action IN ('field_corrected', 'specialty_changed')
      ),
      CONSTRAINT audit_events_document_viewed_ck CHECK (
        action <> 'document_viewed' OR document_id IS NOT NULL
      ),
      CONSTRAINT audit_events_referral_id_fkey
        FOREIGN KEY (referral_id) REFERENCES referrals (id) ON DELETE RESTRICT,
      CONSTRAINT audit_events_document_fkey
        FOREIGN KEY (document_id, referral_id)
        REFERENCES documents (id, referral_id)
        ON DELETE RESTRICT,
      CONSTRAINT audit_events_run_fkey
        FOREIGN KEY (run_id, referral_id)
        REFERENCES processing_runs (id, referral_id)
        ON DELETE RESTRICT
    )
    """,
    """
    ALTER TABLE referrals
      ADD CONSTRAINT referrals_current_run_id_fkey
      FOREIGN KEY (current_run_id) REFERENCES processing_runs (id)
      DEFERRABLE INITIALLY DEFERRED
    """,
    """
    ALTER TABLE processing_runs
      ADD CONSTRAINT processing_runs_ocr_run_id_fkey
      FOREIGN KEY (ocr_run_id) REFERENCES ocr_runs (id)
      ON DELETE RESTRICT
    """,
    """
    CREATE INDEX referrals_status_created_idx
      ON referrals (status, created_at DESC)
    """,
    """
    CREATE UNIQUE INDEX referrals_idempotency_key_uidx
      ON referrals (idempotency_key)
      WHERE idempotency_key IS NOT NULL
    """,
    """
    CREATE UNIQUE INDEX documents_referral_id_uidx ON documents (referral_id)
    """,
    """
    CREATE UNIQUE INDEX documents_s3_key_uidx ON documents (s3_key)
    """,
    """
    CREATE INDEX documents_sha256_idx ON documents (sha256_hex)
    """,
    """
    CREATE UNIQUE INDEX processing_runs_referral_attempt_uidx
      ON processing_runs (referral_id, attempt)
    """,
    """
    CREATE UNIQUE INDEX processing_runs_one_active_uidx
      ON processing_runs (referral_id)
      WHERE status IN ('queued', 'running')
    """,
    """
    CREATE INDEX processing_runs_referral_idx
      ON processing_runs (referral_id, created_at DESC)
    """,
    """
    CREATE INDEX processing_runs_ocr_run_id_idx ON processing_runs (ocr_run_id)
    """,
    """
    CREATE UNIQUE INDEX ocr_runs_document_config_attempt_uidx
      ON ocr_runs (document_id, config_version, attempt)
    """,
    """
    CREATE UNIQUE INDEX ocr_runs_client_request_token_uidx
      ON ocr_runs (client_request_token)
    """,
    """
    CREATE UNIQUE INDEX ocr_runs_one_active_uidx
      ON ocr_runs (document_id)
      WHERE status IN ('queued', 'running')
    """,
    """
    CREATE UNIQUE INDEX ocr_runs_raw_output_s3_key_uidx
      ON ocr_runs (raw_output_s3_key)
      WHERE raw_output_s3_key IS NOT NULL
    """,
    """
    CREATE INDEX ocr_runs_created_by_run_id_idx ON ocr_runs (created_by_run_id)
    """,
    """
    CREATE UNIQUE INDEX ai_runs_processing_run_id_uidx
      ON ai_runs (processing_run_id)
    """,
    """
    CREATE UNIQUE INDEX referral_fields_run_field_uidx
      ON referral_fields (processing_run_id, field_key)
    """,
    """
    CREATE INDEX referral_fields_referral_idx ON referral_fields (referral_id)
    """,
    """
    CREATE UNIQUE INDEX validation_flags_referral_code_field_uidx
      ON validation_flags (referral_id, code, (COALESCE(field_key, '')))
    """,
    """
    CREATE INDEX validation_flags_processing_run_id_idx
      ON validation_flags (processing_run_id)
    """,
    """
    CREATE INDEX audit_events_referral_occurred_idx
      ON audit_events (referral_id, occurred_at)
    """,
    """
    CREATE INDEX audit_events_document_id_idx ON audit_events (document_id)
    """,
    """
    CREATE INDEX audit_events_run_id_idx ON audit_events (run_id)
    """,
    """
    CREATE FUNCTION referral_fields_protect_suggestion()
    RETURNS trigger
    LANGUAGE plpgsql
    SET search_path = pg_catalog, public
    AS $fn$
    BEGIN
      IF NEW.suggested_text IS DISTINCT FROM OLD.suggested_text
         OR NEW.suggested_json IS DISTINCT FROM OLD.suggested_json
         OR NEW.ai_status IS DISTINCT FROM OLD.ai_status
         OR NEW.evidence IS DISTINCT FROM OLD.evidence
         OR NEW.note IS DISTINCT FROM OLD.note
         OR NEW.field_key IS DISTINCT FROM OLD.field_key
         OR NEW.processing_run_id IS DISTINCT FROM OLD.processing_run_id
         OR NEW.referral_id IS DISTINCT FROM OLD.referral_id THEN
        RAISE EXCEPTION 'suggested values are frozen'
          USING ERRCODE = 'check_violation';
      END IF;
      RETURN NEW;
    END;
    $fn$
    """,
    """
    CREATE TRIGGER referral_fields_protect_suggestion
    BEFORE UPDATE ON referral_fields
    FOR EACH ROW
    EXECUTE FUNCTION referral_fields_protect_suggestion()
    """,
    """
    CREATE FUNCTION audit_events_append_only()
    RETURNS trigger
    LANGUAGE plpgsql
    SET search_path = pg_catalog, public
    AS $fn$
    BEGIN
      IF pg_catalog.current_setting('app.allow_audit_delete', true)
           IS DISTINCT FROM 'on' THEN
        RAISE EXCEPTION 'audit_events is append-only'
          USING ERRCODE = 'check_violation';
      END IF;
      IF TG_OP = 'DELETE' THEN
        RETURN OLD;
      END IF;
      RETURN NEW;
    END;
    $fn$
    """,
    """
    CREATE TRIGGER audit_events_append_only
    BEFORE UPDATE OR DELETE ON audit_events
    FOR EACH ROW
    EXECUTE FUNCTION audit_events_append_only()
    """,
    """
    CREATE FUNCTION demo_purge_referral(p_referral_id uuid)
    RETURNS void
    LANGUAGE plpgsql
    SET search_path = pg_catalog, public
    AS $fn$
    BEGIN
      IF current_user IS DISTINCT FROM 'intake_admin'
         AND NOT EXISTS (
           SELECT 1
           FROM pg_catalog.pg_roles
           WHERE rolname = current_user
             AND rolsuper
         ) THEN
        RAISE EXCEPTION 'demo_purge_referral requires the admin role'
          USING ERRCODE = 'insufficient_privilege';
      END IF;

      PERFORM pg_catalog.set_config('app.allow_audit_delete', 'on', true);

      UPDATE public.referrals
         SET current_run_id = NULL
       WHERE id = p_referral_id;

      DELETE FROM public.audit_events WHERE referral_id = p_referral_id;
      DELETE FROM public.validation_flags WHERE referral_id = p_referral_id;
      DELETE FROM public.referral_fields WHERE referral_id = p_referral_id;
      DELETE FROM public.ai_runs WHERE referral_id = p_referral_id;

      UPDATE public.processing_runs
         SET ocr_run_id = NULL
       WHERE referral_id = p_referral_id;

      DELETE FROM public.ocr_runs WHERE referral_id = p_referral_id;
      DELETE FROM public.processing_runs WHERE referral_id = p_referral_id;
      DELETE FROM public.documents WHERE referral_id = p_referral_id;
      DELETE FROM public.referrals WHERE id = p_referral_id;
    END;
    $fn$
    """,
    """
    REVOKE ALL ON SCHEMA public FROM PUBLIC
    """,
    """
    GRANT USAGE ON SCHEMA public TO intake_app
    """,
    """
    GRANT SELECT, INSERT, UPDATE ON
      referrals, documents, processing_runs, ocr_runs, ai_runs,
      referral_fields, validation_flags
    TO intake_app
    """,
    """
    GRANT SELECT, INSERT ON audit_events TO intake_app
    """,
    """
    REVOKE ALL ON FUNCTION referral_fields_protect_suggestion() FROM PUBLIC
    """,
    """
    REVOKE ALL ON FUNCTION audit_events_append_only() FROM PUBLIC
    """,
    """
    REVOKE ALL ON FUNCTION demo_purge_referral(uuid) FROM PUBLIC
    """,
    """
    GRANT EXECUTE ON FUNCTION referral_fields_protect_suggestion() TO intake_app
    """,
    """
    GRANT EXECUTE ON FUNCTION audit_events_append_only() TO intake_app
    """,
)

_DOWNGRADE_SQL = (
    """
    DROP TABLE IF EXISTS
      audit_events,
      validation_flags,
      referral_fields,
      ai_runs,
      ocr_runs,
      processing_runs,
      documents,
      referrals
    CASCADE
    """,
    """
    DROP FUNCTION IF EXISTS demo_purge_referral(uuid)
    """,
    """
    DROP FUNCTION IF EXISTS audit_events_append_only()
    """,
    """
    DROP FUNCTION IF EXISTS referral_fields_protect_suggestion()
    """,
    """
    GRANT USAGE ON SCHEMA public TO PUBLIC
    """,
    """
    REVOKE ALL ON SCHEMA public FROM intake_app
    """,
)


def _require_postgres_18() -> None:
    version = op.get_bind().execute(text("SHOW server_version_num")).scalar_one()
    if int(str(version)) // 10000 != 18:
        raise SystemExit(
            f"PostgreSQL 18 is required (server reports {version}). "
            "Do not substitute another major version."
        )


def _app_password() -> str:
    password = os.environ.get("DB_APP_PASSWORD", "").strip()
    if password == "":
        raise SystemExit(
            "DB_APP_PASSWORD is required in the shell that runs Alembic. "
            "Do not store it in backend/.env."
        )
    return password


def _ensure_app_role(password: str) -> None:
    bind = op.get_bind()
    exists = bind.execute(text("SELECT 1 FROM pg_roles WHERE rolname = 'intake_app'")).scalar()
    verb = "ALTER ROLE" if exists else "CREATE ROLE"
    statement = psql.SQL(
        f"{verb} intake_app WITH LOGIN PASSWORD {{}} NOSUPERUSER NOCREATEDB NOCREATEROLE"
    ).format(psql.Literal(password))
    raw = bind.connection.driver_connection
    raw.execute(statement)


def _database_name() -> str:
    name = op.get_bind().execute(text("SELECT current_database()")).scalar_one()
    if not isinstance(name, str) or _NAME.fullmatch(name) is None:
        raise SystemExit("Refusing to change CONNECT on an unexpected database name.")
    return name


def _run(statements: tuple[str, ...]) -> None:
    bind = op.get_bind()
    for statement in statements:
        bind.exec_driver_sql(statement)


def upgrade() -> None:
    _require_postgres_18()
    _ensure_app_role(_app_password())
    _run(_UPGRADE_SQL)
    op.get_bind().exec_driver_sql(f"GRANT CONNECT ON DATABASE {_database_name()} TO intake_app")


def downgrade() -> None:
    _require_postgres_18()
    _run(_DOWNGRADE_SQL)
    op.get_bind().exec_driver_sql(f"REVOKE CONNECT ON DATABASE {_database_name()} FROM intake_app")
