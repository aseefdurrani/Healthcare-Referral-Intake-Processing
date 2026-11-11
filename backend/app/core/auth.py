"""Cognito access-token checks. Tests supply a local signing key and a fake JWKS client."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any, Protocol

import jwt
from jwt import PyJWK, PyJWKClient

from app.core.config import Settings
from app.core.errors import problem

_MAX_TOKEN_BYTES = 8192
_LEEWAY_SECONDS = 30
# Cognito key ids are base64, so they include "+", "/", and "=".
_KID = re.compile(r"^[A-Za-z0-9+/_=-]{1,128}$")


class JwkClient(Protocol):
    def get_signing_key_from_jwt(self, token: str | bytes) -> PyJWK:
        """Return the key named by the token ``kid``."""


@dataclass(frozen=True, slots=True)
class StaffPrincipal:
    sub: str


def build_jwk_client(settings: Settings) -> PyJWKClient:
    """Cached Cognito JWKS. An unknown ``kid`` refreshes once on that request.

    PyJWT's default cooldown starts on the initial fetch, which would skip the
    refresh the acceptance test requires. ``cooldown_duration=0`` keeps that
    one refresh. The client still caches the key set for an hour.
    """

    return PyJWKClient(
        settings.cognito_jwks_url,
        cache_jwk_set=True,
        lifespan=3600,
        timeout=5,
        cooldown_duration=0,
    )


def parse_bearer(authorization: str | None) -> str:
    if authorization is None or authorization.strip() == "":
        raise problem("AUTH_REQUIRED", reason="missing_token")
    if len(authorization) > _MAX_TOKEN_BYTES + 32:
        raise problem("TOKEN_INVALID", reason="malformed")
    scheme, separator, token = authorization.partition(" ")
    if separator != " " or scheme.lower() != "bearer" or token == "" or " " in token:
        raise problem("TOKEN_INVALID", reason="malformed")
    if len(token) > _MAX_TOKEN_BYTES or token.count(".") != 2:
        raise problem("TOKEN_INVALID", reason="malformed")
    return token


class TokenVerifier:
    """Verify one access token and return the staff ``sub``."""

    def __init__(self, settings: Settings, jwk_client: JwkClient) -> None:
        self._settings = settings
        self._jwk_client = jwk_client

    def verify(self, token: str) -> StaffPrincipal:
        self._require_rs256_header(token)
        signing_key = self._signing_key(token)
        claims = self._decode(token, signing_key)
        if claims.get("token_use") != "access":
            raise problem("TOKEN_INVALID", reason="wrong_token_use")
        client_id = claims.get("client_id")
        if not isinstance(client_id, str) or client_id != self._settings.cognito_app_client_id:
            raise problem("TOKEN_INVALID", reason="wrong_client")
        return StaffPrincipal(sub=self._canonical_sub(claims.get("sub")))

    def _require_rs256_header(self, token: str) -> None:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.InvalidTokenError:
            raise problem("TOKEN_INVALID", reason="malformed") from None
        if header.get("alg") != "RS256":
            raise problem("TOKEN_INVALID", reason="bad_signature")
        kid = header.get("kid")
        if not isinstance(kid, str) or _KID.fullmatch(kid) is None:
            raise problem("TOKEN_INVALID", reason="malformed")

    def _signing_key(self, token: str) -> PyJWK:
        try:
            return self._jwk_client.get_signing_key_from_jwt(token)
        except jwt.PyJWKClientConnectionError as exc:
            raise problem(
                "AUTH_UNAVAILABLE",
                exception_type=type(exc).__name__,
            ) from None
        except jwt.PyJWKClientError:
            raise problem("TOKEN_INVALID", reason="unknown_kid") from None

    def _decode(self, token: str, signing_key: PyJWK) -> dict[str, Any]:
        try:
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                issuer=self._settings.cognito_issuer,
                leeway=_LEEWAY_SECONDS,
                options={
                    "require": ["exp", "iat", "iss", "sub", "token_use"],
                    "verify_aud": False,
                    "verify_iat": True,
                },
            )
        except jwt.ExpiredSignatureError:
            raise problem("TOKEN_EXPIRED", reason="expired") from None
        except jwt.InvalidIssuerError:
            raise problem("TOKEN_INVALID", reason="wrong_issuer") from None
        except jwt.InvalidSignatureError:
            raise problem("TOKEN_INVALID", reason="bad_signature") from None
        except jwt.InvalidAlgorithmError:
            raise problem("TOKEN_INVALID", reason="bad_signature") from None
        except jwt.InvalidIssuedAtError:
            raise problem("TOKEN_INVALID", reason="malformed") from None
        except jwt.MissingRequiredClaimError:
            raise problem("TOKEN_INVALID", reason="malformed") from None
        except jwt.InvalidTokenError:
            raise problem("TOKEN_INVALID", reason="malformed") from None
        if not isinstance(claims, dict):
            raise problem("TOKEN_INVALID", reason="malformed")
        return claims

    def _canonical_sub(self, sub: object) -> str:
        if not isinstance(sub, str):
            raise problem("NOT_AUTHORIZED", reason="not_allow_listed")
        try:
            canonical = str(uuid.UUID(sub))
        except ValueError:
            raise problem("NOT_AUTHORIZED", reason="not_allow_listed") from None
        if canonical != self._settings.allowed_staff_sub:
            raise problem("NOT_AUTHORIZED", reason="not_allow_listed", actor_id=canonical)
        return canonical


def allow_list_is_empty(settings: Settings) -> bool:
    return settings.allowed_staff_sub == ""
