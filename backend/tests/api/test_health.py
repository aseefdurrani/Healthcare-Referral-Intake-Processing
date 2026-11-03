"""Health, docs, request ids, and the loopback server bind."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from tests.conftest import CANARY, assert_problem, build_settings

from app.core.auth import JwkClient
from app.core.config import Settings
from app.main import UVICORN_HOST, UVICORN_PORT, UVICORN_WORKERS, create_app, serve


def test_health_is_ok_and_does_not_log(api_client: TestClient, capsys: object) -> None:
    response = api_client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["x-content-type-options"] == "nosniff"
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert "http.request.completed" not in captured.out


def test_health_does_not_open_aws_or_postgres(
    monkeypatch: object,
    staff_settings: Settings,
    jwk_client: JwkClient,
) -> None:
    def boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("AWS or PostgreSQL was called")

    patch = monkeypatch.setattr
    patch("boto3.client", boom, raising=False)
    patch("sqlalchemy.create_engine", boom, raising=False)
    app = create_app(staff_settings, jwk_client=jwk_client)
    assert not hasattr(app.state, "engine")
    with TestClient(app) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        assert not hasattr(client.app.state, "engine")


def test_valid_request_id_is_echoed(api_client: TestClient) -> None:
    request_id = "3c9a6d0e-5b21-4f7c-8e90-1a2b3c4d5e6f"
    response = api_client.get("/healthz", headers={"X-Request-ID": request_id})
    assert response.headers["x-request-id"] == request_id


def test_invalid_request_id_is_replaced(api_client: TestClient, capsys: object) -> None:
    response = api_client.get("/healthz", headers={"X-Request-ID": CANARY})
    replacement = response.headers["x-request-id"]
    assert replacement != CANARY
    uuid.UUID(replacement)
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert CANARY not in captured.out
    assert CANARY not in captured.err
    assert CANARY not in response.text


def test_unknown_route_is_problem_json(api_client: TestClient) -> None:
    response = api_client.get(f"/{CANARY}")
    body = assert_problem(response, 404, "NOT_FOUND")
    assert CANARY not in response.text
    assert body["correlation_id"] == response.headers["x-request-id"]


def test_docs_are_disabled_outside_local(api_client: TestClient) -> None:
    response = api_client.get("/docs")
    assert_problem(response, 404, "NOT_FOUND")
    assert_problem(api_client.get("/openapi.json"), 404, "NOT_FOUND")


def test_docs_are_public_when_local(
    monkeypatch: object,
    jwk_client: JwkClient,
) -> None:
    class _QuietEngine:
        def dispose(self) -> None:
            return None

    patch = monkeypatch.setattr
    patch("app.main.open_database", lambda _settings: _QuietEngine())
    app = create_app(build_settings(app_env="local"), jwk_client=jwk_client)
    with TestClient(app) as client:
        assert client.get("/docs").status_code == 200
        spec = client.get("/openapi.json")
        assert spec.status_code == 200
        assert "/healthz" in spec.json()["paths"]
        assert "/api/v1/me" in spec.json()["paths"]


def test_no_cors_middleware(staff_settings: Settings, jwk_client: JwkClient) -> None:
    app = create_app(staff_settings, jwk_client=jwk_client)
    names = [middleware.cls.__name__ for middleware in app.user_middleware]
    assert "CORSMiddleware" not in names
    paths = " ".join(str(getattr(route, "path", "")) for route in app.routes)
    assert "transmit" not in paths


def test_serve_binds_loopback(monkeypatch: object) -> None:
    captured: dict[str, object] = {}

    def fake_run(*_args: object, **kwargs: object) -> None:
        captured.update(kwargs)

    monkeypatch.setattr("app.main.uvicorn.run", fake_run)
    serve()
    assert captured["host"] == UVICORN_HOST == "127.0.0.1"
    assert captured["port"] == UVICORN_PORT == 8000
    assert captured["workers"] == UVICORN_WORKERS == 1
    assert captured["access_log"] is False
