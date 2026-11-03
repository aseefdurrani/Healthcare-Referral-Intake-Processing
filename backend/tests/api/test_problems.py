"""Problem responses and logs do not echo request bodies, queries, or exception text."""

from __future__ import annotations

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict
from tests.conftest import CANARY, SigningMaterial, assert_problem, make_token

from app.api.deps import require_staff
from app.core.auth import JwkClient
from app.core.config import Settings
from app.main import create_app


def test_query_and_body_canaries_stay_out_of_logs(
    api_client: TestClient,
    material: SigningMaterial,
    capsys: pytest.CaptureFixture[str],
) -> None:
    token = make_token(material)
    api_client.get("/api/v1/me", params={"q": CANARY}, headers={"Authorization": f"Bearer {token}"})
    posted = api_client.post(
        "/api/v1/me",
        json={"note": CANARY},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert CANARY not in posted.text
    captured = capsys.readouterr()
    text = captured.out + captured.err
    assert CANARY not in text
    assert "q=" not in text
    assert "/api/v1/me" in text


def test_validation_error_omits_the_input_value(
    staff_settings: Settings,
    jwk_client: JwkClient,
    material: SigningMaterial,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class Payload(BaseModel):
        model_config = ConfigDict(extra="forbid")

        version: int

    app = create_app(staff_settings, jwk_client=jwk_client)

    def update(payload: Payload) -> dict[str, str]:
        return {"version": str(payload.version)}

    app.add_api_route(
        "/api/v1/sample",
        update,
        methods=["POST"],
        dependencies=[Depends(require_staff)],
    )
    token = make_token(material)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/sample",
            json={"version": CANARY},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert_problem(response, 422, "VALIDATION_ERROR")
    assert CANARY not in response.text
    assert "input" not in response.json()["errors"][0]
    captured = capsys.readouterr()
    assert CANARY not in captured.out
    assert CANARY not in captured.err


def test_unhandled_exception_logs_type_only(
    staff_settings: Settings,
    jwk_client: JwkClient,
    capsys: pytest.CaptureFixture[str],
) -> None:
    app = create_app(staff_settings, jwk_client=jwk_client)

    def explode() -> None:
        raise RuntimeError(CANARY)

    app.add_api_route("/api/v1/explode", explode, methods=["GET"])
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/v1/explode")
    assert_problem(response, 500, "INTERNAL_ERROR")
    assert CANARY not in response.text
    captured = capsys.readouterr()
    text = captured.out + captured.err
    assert CANARY not in text
    assert "app.error.unhandled" in text
    assert "RuntimeError" in text
