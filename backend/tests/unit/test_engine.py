"""Pool limits and the TLS arguments passed to PostgreSQL."""

from __future__ import annotations

from tests.conftest import build_settings

from app.db.session import (
    MAX_OVERFLOW,
    POOL_PRE_PING,
    POOL_SIZE,
    build_engine,
    psycopg_connect_args,
)


def test_local_engine_disables_tls_and_caps_the_pool() -> None:
    settings = build_settings()
    args = psycopg_connect_args(settings)
    assert args["sslmode"] == "disable"
    assert "sslrootcert" not in args
    engine = build_engine(settings)
    try:
        status = engine.pool.status()
        assert f"Pool size: {POOL_SIZE}" in status
        assert POOL_SIZE == 5
        assert engine.pool._max_overflow == MAX_OVERFLOW == 0  # type: ignore[attr-defined]
        assert engine.pool._pre_ping is POOL_PRE_PING is True  # type: ignore[attr-defined]
        hidden = engine.url.render_as_string(hide_password=True)
        assert "test-only" not in hidden
        assert engine.url.username == "intake_app"
    finally:
        engine.dispose()


def test_remote_engine_sends_the_ca_bundle() -> None:
    settings = build_settings(
        db_host="example.rds.amazonaws.com",
        db_sslmode="verify-full",
        db_sslrootcert="certs/us-east-2-bundle.pem",
    )
    args = psycopg_connect_args(settings)
    assert args["sslmode"] == "verify-full"
    assert args["sslrootcert"] == "certs/us-east-2-bundle.pem"
    engine = build_engine(settings)
    try:
        assert engine.url.host == "example.rds.amazonaws.com"
        assert engine.url.username == "intake_app"
    finally:
        engine.dispose()
