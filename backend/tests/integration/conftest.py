"""PostgreSQL 18 fixtures. The admin password is never read from backend/.env."""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from psycopg import sql
from sqlalchemy import create_engine
from sqlalchemy.engine import URL, Engine
from sqlalchemy.pool import NullPool
from tests.conftest import build_settings

from app.db.session import build_engine

BACKEND = Path(__file__).resolve().parents[2]
_ENV_KEYS = (
    "DB_NAME",
    "DB_PORT",
    "DB_HOST",
    "DB_ADMIN_USER",
    "DB_ADMIN_HOST",
    "DB_ADMIN_PASSWORD",
    "DB_APP_PASSWORD",
    "DB_SSLMODE",
    "DB_SSLROOTCERT",
)


@dataclass
class Database:
    user: str
    port: int
    password: str
    admin_engine: Engine
    app_engine: Engine


def _app_password() -> str:
    path = BACKEND / ".env"
    if not path.is_file():
        pytest.fail("backend/.env must hold DB_PASSWORD for intake_app.")
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("DB_PASSWORD="):
            value = line.split("=", 1)[1].strip()
            if value and not value.startswith("<"):
                return value
    pytest.fail("Set DB_PASSWORD in backend/.env to the intake_app password.")
    return ""


def _discover() -> tuple[str, int]:
    user = os.environ.get("USER") or "postgres"
    notes: list[str] = []
    for port in (5433, 5432):
        try:
            with psycopg.connect(
                host="127.0.0.1",
                port=port,
                user=user,
                dbname="postgres",
                sslmode="disable",
                connect_timeout=3,
            ) as conn:
                row = conn.execute("SHOW server_version_num").fetchone()
        except Exception as exc:
            notes.append(f"{port}:{exc.__class__.__name__}")
            continue
        if row is None:
            notes.append(f"{port}:no version")
            continue
        major = int(row[0]) // 10000
        if major != 18:
            notes.append(f"{port}:postgresql-{major}")
            continue
        return user, port
    pytest.fail("PostgreSQL 18 is not available. " + "; ".join(notes))
    return user, 0


def _ensure_database(user: str, port: int, name: str) -> None:
    with psycopg.connect(
        host="127.0.0.1",
        port=port,
        user=user,
        dbname="postgres",
        sslmode="disable",
        autocommit=True,
    ) as conn:
        row = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone()
        if row is None:
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))


def migrate(database: str, revision: str, *, user: str, port: int, password: str) -> None:
    previous = {key: os.environ.get(key) for key in _ENV_KEYS}
    os.environ["DB_NAME"] = database
    os.environ["DB_PORT"] = str(port)
    os.environ["DB_HOST"] = "127.0.0.1"
    os.environ["DB_ADMIN_USER"] = user
    os.environ["DB_ADMIN_HOST"] = "127.0.0.1"
    os.environ["DB_APP_PASSWORD"] = password
    os.environ["DB_SSLMODE"] = "disable"
    os.environ["DB_SSLROOTCERT"] = ""
    os.environ.pop("DB_ADMIN_PASSWORD", None)
    try:
        cfg = Config(str(BACKEND / "alembic.ini"))
        if revision == "head":
            command.upgrade(cfg, "head")
        elif revision == "base":
            command.downgrade(cfg, "base")
        else:
            raise AssertionError(revision)
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _sync_app_password(user: str, port: int, password: str) -> None:
    with psycopg.connect(
        host="127.0.0.1",
        port=port,
        user=user,
        dbname="postgres",
        sslmode="disable",
        autocommit=True,
    ) as conn:
        exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = 'intake_app'").fetchone()
        if exists is None:
            return
        conn.execute(
            sql.SQL(
                "ALTER ROLE intake_app WITH LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE"
            ).format(sql.Literal(password))
        )


@pytest.fixture(scope="session")
def database() -> Iterator[Database]:
    user, port = _discover()
    password = _app_password()
    _ensure_database(user, port, "intake")
    _ensure_database(user, port, "intake_test")
    migrate("intake", "head", user=user, port=port, password=password)
    migrate("intake_test", "head", user=user, port=port, password=password)
    _sync_app_password(user, port, password)
    admin_engine = create_engine(
        URL.create(
            "postgresql+psycopg",
            username=user,
            host="127.0.0.1",
            port=port,
            database="intake_test",
        ),
        poolclass=NullPool,
        hide_parameters=True,
        connect_args={"sslmode": "disable", "application_name": "cortrucent-test-admin"},
    )
    app_engine = build_engine(
        build_settings(
            db_host="127.0.0.1",
            db_port=port,
            db_name="intake_test",
            db_user="intake_app",
            db_password=password,
            db_sslmode="disable",
            db_sslrootcert="",
        )
    )
    try:
        yield Database(
            user=user,
            port=port,
            password=password,
            admin_engine=admin_engine,
            app_engine=app_engine,
        )
    finally:
        app_engine.dispose()
        admin_engine.dispose()
