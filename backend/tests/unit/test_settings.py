"""Settings load from the environment and fail closed on TLS and the staff allow list."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from pydantic import ValidationError
from tests.conftest import build_settings

from app.core.config import AppEnv, Settings, load_settings
from app.db.session import MAX_OVERFLOW, POOL_PRE_PING, POOL_SIZE


def test_settings_are_constructed_without_a_database() -> None:
    settings = build_settings()
    assert settings.app_env == AppEnv.TEST
    assert settings.db_host == "127.0.0.1"
    assert settings.db_sslmode == "disable"
    assert settings.allowed_staff_sub == "11111111-1111-4111-8111-111111111111"
    assert settings.cognito_issuer.startswith("https://cognito-idp.us-east-2.amazonaws.com/")


def test_remote_host_without_verify_full_exits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DB_HOST", "example.rds.amazonaws.com")
    monkeypatch.setenv("DB_SSLMODE", "disable")
    with pytest.raises(SystemExit):
        load_settings()


def test_localhost_disable_starts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DB_HOST", "localhost")
    monkeypatch.setenv("DB_SSLMODE", "disable")
    monkeypatch.setenv("DB_SSLROOTCERT", "")
    settings = load_settings()
    assert settings.db_host == "localhost"
    assert settings.db_sslmode == "disable"


def test_loopback_disable_starts() -> None:
    settings = Settings(
        app_env="test",
        db_host="127.0.0.1",
        db_sslmode="disable",
        allowed_staff_sub="",
    )
    assert settings.db_sslmode == "disable"


def test_remote_verify_full_requires_a_bundle() -> None:
    with pytest.raises(ValidationError):
        Settings(
            app_env="test",
            db_host="example.rds.amazonaws.com",
            db_sslmode="verify-full",
            db_sslrootcert="",
        )


def test_invalid_staff_sub_is_rejected() -> None:
    with pytest.raises(ValidationError):
        build_settings(allowed_staff_sub="not-a-uuid")


def test_empty_staff_sub_fails_closed() -> None:
    settings = build_settings(allowed_staff_sub="")
    assert settings.allowed_staff_sub == ""


def test_env_file_loads_only_for_local(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("S3_BUCKET_NAME", raising=False)
    (tmp_path / ".env").write_text("S3_BUCKET_NAME=from-dotenv-only\n", encoding="utf-8")
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("DB_HOST", "127.0.0.1")
    monkeypatch.setenv("DB_SSLMODE", "disable")
    assert load_settings().s3_bucket_name == "from-dotenv-only"
    monkeypatch.setenv("APP_ENV", "test")
    assert load_settings().s3_bucket_name == ""


def test_example_lists_placeholders_and_no_secrets() -> None:
    example = Path(__file__).resolve().parents[2] / ".env.example"
    text = example.read_text(encoding="utf-8")
    assert "DB_PASSWORD=<local-password>" in text
    assert "intake_admin" not in text
    for forbidden in (
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "DATABASE_URL",
        "NEXT_PUBLIC_",
    ):
        assert forbidden not in text
    names = {
        line.split("=", 1)[0]
        for line in text.splitlines()
        if line and not line.startswith("#") and "=" in line
    }
    for field_name in Settings.model_fields:
        assert field_name.upper() in names


def test_pool_constants_match_the_single_worker_cap() -> None:
    assert POOL_SIZE == 5
    assert MAX_OVERFLOW == 0
    assert POOL_PRE_PING is True


def test_staff_sub_is_canonical() -> None:
    raw = "AAAAAAAA-BBBB-4CCC-8DDD-EEEEEEEEEEEE"
    settings = build_settings(allowed_staff_sub=raw)
    assert settings.allowed_staff_sub == str(uuid.UUID(raw))
