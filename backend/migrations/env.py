"""Alembic connects as the admin role, never as ``intake_app``.

``DB_ADMIN_PASSWORD`` is read from the process environment when it is set.
This module does not load ``backend/.env``. The application password used to
create ``intake_app`` is ``DB_APP_PASSWORD``, also from the shell only.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from alembic import context
from sqlalchemy import create_engine, pool
from sqlalchemy.engine import URL

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.db import models as _models  # noqa: E402, F401
from app.db.base import Base  # noqa: E402

target_metadata = Base.metadata
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1"})


def _required(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None or value.strip() == "":
        raise SystemExit(f"{name} is required to run Alembic.")
    return value.strip()


def _admin_engine() -> object:
    user = _required("DB_ADMIN_USER", os.environ.get("USER", "postgres"))
    host = os.environ.get("DB_ADMIN_HOST", os.environ.get("DB_HOST", "127.0.0.1")).strip()
    database = _required("DB_NAME", "intake")
    password = os.environ.get("DB_ADMIN_PASSWORD") or None
    connect_args: dict[str, str | int] = {
        "connect_timeout": 5,
        "application_name": "cortrucent-alembic",
    }
    if host.startswith("/"):
        connect_args["host"] = host
        connect_args["sslmode"] = "disable"
        url = URL.create(
            "postgresql+psycopg",
            username=user,
            password=password,
            database=database,
        )
    else:
        local = host in _LOCAL_HOSTS
        if local:
            connect_args["sslmode"] = "disable"
        else:
            mode = os.environ.get("DB_SSLMODE", "").strip()
            cert = os.environ.get("DB_SSLROOTCERT", "").strip()
            if mode != "verify-full" or cert == "":
                raise SystemExit(
                    "Admin migrations to a non-local host require "
                    "DB_SSLMODE=verify-full and DB_SSLROOTCERT."
                )
            connect_args["sslmode"] = "verify-full"
            connect_args["sslrootcert"] = cert
        url = URL.create(
            "postgresql+psycopg",
            username=user,
            password=password,
            host=host,
            port=int(os.environ.get("DB_PORT", "5432")),
            database=database,
        )
    return create_engine(
        url,
        poolclass=pool.NullPool,
        hide_parameters=True,
        connect_args=connect_args,
    )


def run_migrations_offline() -> None:
    raise SystemExit("Run Alembic online against PostgreSQL 18.")


def run_migrations_online() -> None:
    connectable = _admin_engine()
    try:
        with connectable.connect() as connection:  # type: ignore[attr-defined]
            context.configure(connection=connection, target_metadata=target_metadata)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        connectable.dispose()  # type: ignore[attr-defined]


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
