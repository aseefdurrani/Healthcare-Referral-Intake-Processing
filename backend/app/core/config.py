"""Process settings. Tests construct ``Settings`` directly. ``.env`` loads only for local."""

from __future__ import annotations

import os
import sys
import uuid
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import Field, SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_LOCAL_DB_HOSTS = frozenset({"localhost", "127.0.0.1"})


class AppEnv(StrEnum):
    LOCAL = "local"
    TEST = "test"
    DEPLOYED = "deployed"


class Settings(BaseSettings):
    """One process reads these names. There is no ``DATABASE_URL`` secret."""

    model_config = SettingsConfigDict(
        env_file=None,
        extra="ignore",
        case_sensitive=False,
        hide_input_in_errors=True,
    )

    app_env: AppEnv = AppEnv.LOCAL
    app_version: str = "dev"
    aws_region: Literal["us-east-2"] = "us-east-2"
    aws_profile: str | None = None
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_sink: Literal["stdout", "cloudwatch"] = "stdout"
    s3_bucket_name: str = ""
    s3_sse_mode: Literal["aws:kms"] = "aws:kms"

    db_host: str = "127.0.0.1"
    db_port: int = Field(default=5432, ge=1, le=65535)
    db_name: str = "intake"
    db_user: str = "intake_app"
    db_password: SecretStr = SecretStr("")
    db_sslmode: Literal["disable", "verify-full"] = "disable"
    db_sslrootcert: str = ""

    cognito_user_pool_id: str = ""
    cognito_app_client_id: str = ""
    allowed_staff_sub: str = ""

    bedrock_model_id: str = "us.anthropic.claude-sonnet-5-5"
    bedrock_read_timeout_seconds: int = Field(default=120, ge=1, le=300)
    bedrock_max_output_tokens: int = Field(default=6000, ge=1, le=6000)
    ai_enabled: bool = True
    textract_feature_types: str = "LAYOUT,FORMS,TABLES"
    max_upload_bytes: int = Field(default=10_000_000, ge=1, le=10_000_000)
    max_pdf_pages: int = Field(default=10, ge=1, le=10)
    processing_max_workers: int = Field(default=2, ge=1, le=2)
    processing_run_timeout_seconds: int = Field(default=300, ge=1, le=300)
    processing_max_attempts: int = Field(default=3, ge=1, le=3)
    textract_usd_per_page: Decimal = Decimal("0.065")
    bedrock_input_usd_per_1k_tokens: Decimal = Decimal("0.0022")
    bedrock_output_usd_per_1k_tokens: Decimal = Decimal("0.011")
    pricing_rates_as_of: date = date(2026, 11, 3)

    @field_validator("log_level", mode="before")
    @classmethod
    def _uppercase_level(cls, value: object) -> object:
        if isinstance(value, str):
            return value.upper()
        return value

    @field_validator("aws_profile", mode="before")
    @classmethod
    def _blank_profile(cls, value: object) -> object:
        if value == "":
            return None
        return value

    @field_validator("db_host", "db_sslmode", "db_sslrootcert", "allowed_staff_sub", mode="before")
    @classmethod
    def _strip_text(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("allowed_staff_sub")
    @classmethod
    def _staff_sub_is_uuid_or_empty(cls, value: str) -> str:
        if value == "":
            return ""
        try:
            return str(uuid.UUID(value))
        except ValueError as exc:
            raise ValueError("ALLOWED_STAFF_SUB must be a UUID") from exc

    @model_validator(mode="after")
    def _enforce_database_tls(self) -> Settings:
        local = self.db_host in _LOCAL_DB_HOSTS
        if self.db_sslmode == "disable" and not local:
            raise ValueError(
                "DB_SSLMODE must be verify-full and DB_SSLROOTCERT is required "
                "when DB_HOST is not local"
            )
        if self.db_sslmode == "verify-full" and not self.db_sslrootcert.strip():
            raise ValueError("DB_SSLROOTCERT is required when DB_SSLMODE is verify-full")
        return self

    @property
    def cognito_issuer(self) -> str:
        return f"https://cognito-idp.{self.aws_region}.amazonaws.com/{self.cognito_user_pool_id}"

    @property
    def cognito_jwks_url(self) -> str:
        return f"{self.cognito_issuer}/.well-known/jwks.json"

    @property
    def textract_features(self) -> list[str]:
        return [part.strip() for part in self.textract_feature_types.split(",") if part.strip()]


def load_settings() -> Settings:
    """Load settings, reading ``.env`` only when ``APP_ENV=local``. Exit on invalid config."""

    app_env = os.environ.get("APP_ENV", "local")
    env_file = ".env" if app_env == "local" else None
    try:
        return Settings(_env_file=env_file)  # type: ignore[call-arg]
    except ValidationError as exc:
        names = sorted({str(err["loc"][0]) for err in exc.errors() if err.get("loc")})
        label = ", ".join(names) if names else "settings"
        print(f"Invalid configuration ({label}). Refusing to start.", file=sys.stderr)
        raise SystemExit(1) from exc
