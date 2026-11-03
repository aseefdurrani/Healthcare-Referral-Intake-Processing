"""Shared fixtures. Environment is pinned before the app is imported."""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from jwt.algorithms import RSAAlgorithm

os.environ.update(
    {
        "APP_ENV": "test",
        "APP_VERSION": "test",
        "AWS_REGION": "us-east-2",
        "LOG_LEVEL": "INFO",
        "LOG_SINK": "stdout",
        "S3_SSE_MODE": "aws:kms",
        "DB_HOST": "127.0.0.1",
        "DB_PORT": "5432",
        "DB_NAME": "intake",
        "DB_USER": "intake_app",
        "DB_PASSWORD": "test-only",
        "DB_SSLMODE": "disable",
        "DB_SSLROOTCERT": "",
        "COGNITO_USER_POOL_ID": "us-east-2_TESTPOOL1",
        "COGNITO_APP_CLIENT_ID": "test-client-id",
        "ALLOWED_STAFF_SUB": "",
        "BEDROCK_MODEL_ID": "us.anthropic.claude-sonnet-5-5",
        "BEDROCK_READ_TIMEOUT_SECONDS": "120",
        "BEDROCK_MAX_OUTPUT_TOKENS": "6000",
        "AI_ENABLED": "true",
        "TEXTRACT_FEATURE_TYPES": "LAYOUT,FORMS,TABLES",
        "MAX_UPLOAD_BYTES": "10000000",
        "MAX_PDF_PAGES": "10",
        "PROCESSING_MAX_WORKERS": "2",
        "PROCESSING_RUN_TIMEOUT_SECONDS": "300",
        "PROCESSING_MAX_ATTEMPTS": "3",
        "TEXTRACT_USD_PER_PAGE": "0.065",
        "BEDROCK_INPUT_USD_PER_1K_TOKENS": "0.0022",
        "BEDROCK_OUTPUT_USD_PER_1K_TOKENS": "0.011",
        "PRICING_RATES_AS_OF": "2026-11-03",
    }
)

from app.core.auth import JwkClient  # noqa: E402
from app.core.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402

STAFF_SUB = "11111111-1111-4111-8111-111111111111"
OTHER_SUB = "22222222-2222-4222-8222-222222222222"
CLIENT_ID = "test-client-id"
POOL_ID = "us-east-2_TESTPOOL1"
REGION = "us-east-2"
KID = "test-key"
CANARY = "ZZCANARY-NAME-7F3A"
EMAIL = "staff-canary@example.com"
ISSUER = f"https://cognito-idp.{REGION}.amazonaws.com/{POOL_ID}"


@dataclass(frozen=True)
class SigningMaterial:
    private_key: rsa.RSAPrivateKey
    jwk_dict: dict[str, object]
    jwk: jwt.PyJWK


class MapJwkClient:
    """In-memory JWKS. Unknown key ids fail without a network call."""

    def __init__(self, keys: dict[str, jwt.PyJWK]) -> None:
        self.keys = keys

    def get_signing_key_from_jwt(self, token: str | bytes) -> jwt.PyJWK:
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")
        if not isinstance(kid, str) or kid not in self.keys:
            raise jwt.PyJWKClientError(f'Unable to find a signing key that matches: "{kid}"')
        return self.keys[kid]


def build_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "app_env": "test",
        "app_version": "test",
        "aws_region": REGION,
        "log_level": "INFO",
        "db_host": "127.0.0.1",
        "db_sslmode": "disable",
        "db_sslrootcert": "",
        "db_password": "test-only",
        "cognito_user_pool_id": POOL_ID,
        "cognito_app_client_id": CLIENT_ID,
        "allowed_staff_sub": STAFF_SUB,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def make_token(
    material: SigningMaterial,
    *,
    sub: str = STAFF_SUB,
    issuer: str = ISSUER,
    client_id: str | None = CLIENT_ID,
    token_use: str = "access",
    exp: int | None = None,
    iat: int | None = None,
    kid: str = KID,
    algorithm: str = "RS256",
) -> str:
    now = int(time.time())
    payload: dict[str, object] = {
        "sub": sub,
        "iss": issuer,
        "token_use": token_use,
        "exp": now + 600 if exp is None else exp,
        "iat": now if iat is None else iat,
        "email": EMAIL,
        "name": CANARY,
    }
    if client_id is not None:
        payload["client_id"] = client_id
    encoded = jwt.encode(payload, material.private_key, algorithm=algorithm, headers={"kid": kid})
    if isinstance(encoded, bytes):
        return encoded.decode("ascii")
    return encoded


def assert_problem(response: object, status: int, code: str) -> dict[str, object]:
    assert getattr(response, "status_code", None) == status
    headers = response.headers  # type: ignore[attr-defined]
    assert headers["content-type"].startswith("application/problem+json")
    body = response.json()  # type: ignore[attr-defined]
    assert body["status"] == status
    assert body["code"] == code
    assert body["correlation_id"] == headers["x-request-id"]
    uuid.UUID(body["correlation_id"])
    assert body["type"] == f"urn:cortrucent:problem:{code.lower().replace('_', '-')}"
    return body


@pytest.fixture(scope="session")
def material() -> SigningMaterial:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk_dict = json.loads(RSAAlgorithm.to_jwk(private_key.public_key()))
    jwk_dict["kid"] = KID
    jwk_dict["use"] = "sig"
    jwk_dict["alg"] = "RS256"
    return SigningMaterial(
        private_key=private_key,
        jwk_dict=jwk_dict,
        jwk=jwt.PyJWK.from_dict(jwk_dict),
    )


@pytest.fixture
def jwk_client(material: SigningMaterial) -> JwkClient:
    return MapJwkClient({KID: material.jwk})


@pytest.fixture
def staff_settings() -> Settings:
    return build_settings()


@pytest.fixture
def api_client(staff_settings: Settings, jwk_client: JwkClient) -> object:
    app = create_app(staff_settings, jwk_client=jwk_client)
    with TestClient(app) as client:
        yield client
