"""Engine for the ``intake_app`` role.

The admin password is not a setting. Alembic reads it from the operator shell.
``disable`` is legal only for ``localhost`` and ``127.0.0.1``. Every other host
must use ``verify-full`` and the RDS CA bundle, or the process exits.

The us-east-2 RDS CA bundle is ``certs/us-east-2-bundle.pem``.
RSA2048 G1 serial 96259E6906463E23BBFDA3050B275C96 expires 2061-05-22.
RSA4096 G1 serial FBEF77E302729F04DFA947CE2BF4C613 expires 2121-05-22.
ECC384 G1 serial 19CCED472578D29C8C29B35E48DFAF5D expires 2121-05-22.
"""

from __future__ import annotations

import logging

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.core.logging import log_event

POOL_SIZE = 5
MAX_OVERFLOW = 0
POOL_PRE_PING = True
_CONNECT_TIMEOUT_SECONDS = 5
_LOCAL_DB_HOSTS = frozenset({"localhost", "127.0.0.1"})


def psycopg_connect_args(settings: Settings) -> dict[str, str | int]:
    """Connection arguments for psycopg. Invalid TLS exits before a socket opens."""

    local = settings.db_host in _LOCAL_DB_HOSTS
    if settings.db_sslmode == "disable" and not local:
        raise SystemExit(1)
    if not local and settings.db_sslmode != "verify-full":
        raise SystemExit(1)
    if settings.db_sslmode == "verify-full" and not settings.db_sslrootcert.strip():
        raise SystemExit(1)
    args: dict[str, str | int] = {
        "sslmode": settings.db_sslmode,
        "connect_timeout": _CONNECT_TIMEOUT_SECONDS,
        "application_name": "cortrucent-intake",
    }
    if settings.db_sslmode == "verify-full":
        args["sslrootcert"] = settings.db_sslrootcert
    return args


def build_engine(settings: Settings) -> Engine:
    """Pool of five connections, no overflow, with a liveness check on checkout."""

    url = URL.create(
        drivername="postgresql+psycopg",
        username=settings.db_user,
        password=settings.db_password.get_secret_value(),
        host=settings.db_host,
        port=settings.db_port,
        database=settings.db_name,
    )
    return create_engine(
        url,
        pool_size=POOL_SIZE,
        max_overflow=MAX_OVERFLOW,
        pool_pre_ping=POOL_PRE_PING,
        hide_parameters=True,
        connect_args=psycopg_connect_args(settings),
    )


def log_database_tls(engine: Engine) -> None:
    """Record only the boolean from ``pg_stat_ssl``. The URL is not logged."""

    with engine.connect() as connection:
        ssl_on = connection.execute(
            text("SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()")
        ).scalar_one()
    log_event(logging.INFO, "db.connected", ssl=bool(ssl_on))


def open_database(settings: Settings) -> Engine:
    """Open the pool and confirm a connection. Dispose it if the check fails."""

    engine = build_engine(settings)
    try:
        log_database_tls(engine)
    except Exception:
        engine.dispose()
        raise
    return engine


def build_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
