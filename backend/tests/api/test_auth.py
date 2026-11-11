"""G acceptance tests T1–T14. Tokens are signed with a local RSA key."""

from __future__ import annotations

import base64
import json
import time

import jwt
import pytest
from fastapi.testclient import TestClient
from tests.conftest import (
    CANARY,
    CLIENT_ID,
    EMAIL,
    KID,
    OTHER_SUB,
    STAFF_SUB,
    MapJwkClient,
    SigningMaterial,
    assert_problem,
    build_settings,
    make_token,
)

from app.api.deps import require_staff
from app.core.auth import build_jwk_client
from app.core.config import Settings
from app.main import create_app


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _none_token() -> str:
    def encode(value: dict[str, object]) -> str:
        raw = json.dumps(value, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    header = {"alg": "none", "typ": "JWT", "kid": KID}
    now = int(time.time())
    payload = {
        "sub": STAFF_SUB,
        "iss": "https://cognito-idp.us-east-2.amazonaws.com/us-east-2_TESTPOOL1",
        "token_use": "access",
        "client_id": CLIENT_ID,
        "exp": now + 600,
        "iat": now,
        "email": EMAIL,
    }
    return f"{encode(header)}.{encode(payload)}."


def _calls(dependant: object) -> set[object]:
    found: set[object] = set()
    for dependency in getattr(dependant, "dependencies", []):
        found.add(getattr(dependency, "call", None))
        found.update(_calls(dependency))
    return found


def test_t1_missing_authorization_is_401(api_client: TestClient) -> None:
    response = api_client.get("/api/v1/me")
    assert_problem(response, 401, "AUTH_REQUIRED")
    assert response.headers["www-authenticate"] == "Bearer"


def test_t2_malformed_token_is_401(api_client: TestClient) -> None:
    response = api_client.get("/api/v1/me", headers=_bearer("not-a-token"))
    assert_problem(response, 401, "TOKEN_INVALID")
    response = api_client.get("/api/v1/me", headers=_bearer("a.b.c"))
    assert_problem(response, 401, "TOKEN_INVALID")


def test_t3_rejects_none_and_hs256(api_client: TestClient) -> None:
    response = api_client.get("/api/v1/me", headers=_bearer(_none_token()))
    assert_problem(response, 401, "TOKEN_INVALID")
    hs_token = jwt.encode(
        {"sub": STAFF_SUB},
        "x" * 32,
        algorithm="HS256",
        headers={"kid": KID},
    )
    response = api_client.get("/api/v1/me", headers=_bearer(hs_token))
    assert_problem(response, 401, "TOKEN_INVALID")


def test_t4_wrong_issuer_is_401(api_client: TestClient, material: SigningMaterial) -> None:
    token = make_token(
        material, issuer="https://cognito-idp.us-east-1.amazonaws.com/us-east-1_OTHER"
    )
    response = api_client.get("/api/v1/me", headers=_bearer(token))
    assert_problem(response, 401, "TOKEN_INVALID")


def test_t5_id_token_is_401(api_client: TestClient, material: SigningMaterial) -> None:
    token = make_token(material, token_use="id", client_id=None)
    response = api_client.get("/api/v1/me", headers=_bearer(token))
    assert_problem(response, 401, "TOKEN_INVALID")


def test_t6_wrong_client_is_401(api_client: TestClient, material: SigningMaterial) -> None:
    token = make_token(material, client_id="other-client")
    response = api_client.get("/api/v1/me", headers=_bearer(token))
    assert_problem(response, 401, "TOKEN_INVALID")


def test_t7_expiry_respects_thirty_second_leeway(
    api_client: TestClient,
    material: SigningMaterial,
) -> None:
    now = int(time.time())
    expired = make_token(material, exp=now - 31, iat=now - 120)
    response = api_client.get("/api/v1/me", headers=_bearer(expired))
    assert_problem(response, 401, "TOKEN_EXPIRED")
    within = make_token(material, exp=now - 20, iat=now - 120)
    response = api_client.get("/api/v1/me", headers=_bearer(within))
    assert response.status_code == 200


def test_cognito_base64_kid_is_accepted(
    material: SigningMaterial,
    staff_settings: Settings,
) -> None:
    kid = "r/DbeUCgF37OLTCIMbHKv7ctvnBAdYyT/CH6jrD6q2Y="
    app = create_app(staff_settings, jwk_client=MapJwkClient({kid: material.jwk}))
    token = make_token(material, kid=kid)
    with TestClient(app) as client:
        response = client.get("/api/v1/me", headers=_bearer(token))
    assert response.status_code == 200


def test_t8_unknown_kid_refreshes_once_then_401(
    monkeypatch: pytest.MonkeyPatch,
    material: SigningMaterial,
    staff_settings: Settings,
) -> None:
    fetches = {"count": 0}

    def fetch(self: object) -> dict[str, object]:
        fetches["count"] += 1
        return {"keys": [material.jwk_dict]}

    monkeypatch.setattr(jwt.PyJWKClient, "fetch_data", fetch)
    app = create_app(staff_settings)
    token = make_token(material, kid="missing-kid")
    with TestClient(app) as client:
        response = client.get("/api/v1/me", headers=_bearer(token))
    assert_problem(response, 401, "TOKEN_INVALID")
    assert fetches["count"] == 2


def test_t9_unlisted_sub_is_403(api_client: TestClient, material: SigningMaterial) -> None:
    token = make_token(material, sub=OTHER_SUB)
    response = api_client.get("/api/v1/me", headers=_bearer(token))
    assert_problem(response, 403, "NOT_AUTHORIZED")
    assert "www-authenticate" not in response.headers


def test_t10_allow_listed_sub_is_200(api_client: TestClient, material: SigningMaterial) -> None:
    token = make_token(material)
    response = api_client.get("/api/v1/me", headers=_bearer(token))
    assert response.status_code == 200
    assert response.json() == {"user_id": STAFF_SUB, "username": "staff"}
    assert "email" not in response.json()
    assert response.headers["cache-control"] == "no-store"


def test_t11_jwks_unreachable_and_cached_key(
    monkeypatch: pytest.MonkeyPatch,
    material: SigningMaterial,
    staff_settings: Settings,
) -> None:
    state = {"fail": True}

    def fetch(self: object) -> dict[str, object]:
        if state["fail"]:
            raise jwt.PyJWKClientConnectionError('Fail to fetch data from the url, err: "down"')
        return {"keys": [material.jwk_dict]}

    monkeypatch.setattr(jwt.PyJWKClient, "fetch_data", fetch)
    cold = build_jwk_client(staff_settings)
    token = make_token(material)
    with TestClient(create_app(staff_settings, jwk_client=cold)) as client:
        response = client.get("/api/v1/me", headers=_bearer(token))
    assert_problem(response, 503, "AUTH_UNAVAILABLE")

    state["fail"] = False
    warm = build_jwk_client(staff_settings)
    warm.get_signing_key(KID)
    state["fail"] = True
    with TestClient(create_app(staff_settings, jwk_client=warm)) as client:
        response = client.get("/api/v1/me", headers=_bearer(token))
    assert response.status_code == 200


def _iter_routes(app: object) -> list[object]:
    found: list[object] = []
    for route in getattr(app, "routes", []):
        original = getattr(route, "original_router", None)
        if original is not None:
            found.extend(getattr(original, "routes", []))
            continue
        found.append(route)
    return found


def test_t12_every_protected_route_depends_on_staff(jwk_client: object) -> None:
    app = create_app(build_settings(app_env="local"), jwk_client=jwk_client)  # type: ignore[arg-type]
    public = {"/healthz", "/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"}
    seen_health = False
    seen_me = False
    for route in _iter_routes(app):
        path = getattr(route, "path", None)
        if not isinstance(path, str):
            continue
        if path in public:
            if path == "/healthz":
                seen_health = True
                assert require_staff not in _calls(getattr(route, "dependant", None))
            continue
        seen_me = seen_me or path == "/api/v1/me"
        assert require_staff in _calls(getattr(route, "dependant", None)), path
    assert seen_health and seen_me


def test_t13_logs_omit_token_email_and_canary(
    api_client: TestClient,
    material: SigningMaterial,
    capsys: pytest.CaptureFixture[str],
) -> None:
    token = make_token(material)
    api_client.get("/api/v1/me")
    api_client.get("/api/v1/me", headers={"Authorization": "Bearer not-a-token"})
    api_client.get("/api/v1/me", headers=_bearer(token))
    api_client.get("/api/v1/me", params={"access_token": token})
    api_client.get(f"/{CANARY}", headers={"Authorization": f"Bearer {token}"})
    captured = capsys.readouterr()
    text = captured.out + captured.err
    assert "http.request.completed" in text
    assert token not in text
    assert f"Bearer {token}" not in text
    assert EMAIL not in text
    assert CANARY not in text
    assert "Authorization" not in text


def test_t14_empty_allow_list_rejects_protected_routes(
    material: SigningMaterial,
    jwk_client: object,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = build_settings(allowed_staff_sub="")
    app = create_app(settings, jwk_client=jwk_client)  # type: ignore[arg-type]
    token = make_token(material)
    with TestClient(app) as client:
        missing = client.get("/api/v1/me")
        presented = client.get("/api/v1/me", headers=_bearer(token))
        health = client.get("/healthz")
    assert_problem(missing, 403, "NOT_AUTHORIZED")
    assert_problem(presented, 403, "NOT_AUTHORIZED")
    assert health.status_code == 200
    captured = capsys.readouterr()
    text = captured.out + captured.err
    assert "allow_list_empty" in text
    assert token not in text
    assert EMAIL not in text
