"""Request dependencies. ``require_staff`` is the router-level gate for ``/api/v1``."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterator
from typing import Annotated

from fastapi import Header, Request
from sqlalchemy.orm import Session

from app.core.auth import StaffPrincipal, TokenVerifier, allow_list_is_empty, parse_bearer
from app.core.config import Settings
from app.core.errors import AppError, problem, request_id_from
from app.core.logging import log_event, route_template


def _log_auth_failure(request: Request, exc: AppError) -> None:
    method = request.method
    route = route_template(request.scope)
    if exc.code == "AUTH_UNAVAILABLE":
        log_event(
            logging.ERROR,
            "auth.jwks_unavailable",
            exception_type=exc.exception_type or "PyJWKClientError",
        )
        return
    if exc.code == "NOT_AUTHORIZED":
        fields: dict[str, object] = {"method": method, "route": route}
        if exc.reason is not None:
            fields["reason"] = exc.reason
        if exc.actor_id is not None:
            fields["actor_id"] = exc.actor_id
        log_event(logging.WARNING, "auth.forbidden", **fields)
        return
    log_event(
        logging.WARNING,
        "auth.denied",
        method=method,
        route=route,
        reason=exc.reason or "malformed",
    )


def require_staff(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    """Accept one Cognito access token whose ``sub`` is the configured staff id."""

    settings: Settings = request.app.state.settings
    if allow_list_is_empty(settings):
        failure = AppError(
            status=403,
            code="NOT_AUTHORIZED",
            title="Not authorized",
            detail="This account cannot use the intake API.",
            reason="allow_list_empty",
        )
        _log_auth_failure(request, failure)
        raise failure
    verifier: TokenVerifier = request.app.state.verifier
    try:
        token = parse_bearer(authorization)
        principal = verifier.verify(token)
    except AppError as exc:
        _log_auth_failure(request, exc)
        raise
    request.state.staff = principal
    request.state.actor_id = principal.sub


def get_current_staff(request: Request) -> StaffPrincipal:
    staff = getattr(request.state, "staff", None)
    if not isinstance(staff, StaffPrincipal):
        failure = AppError(
            status=401,
            code="AUTH_REQUIRED",
            title="Authentication required",
            detail="Provide a Bearer access token.",
            headers={"WWW-Authenticate": "Bearer"},
            reason="missing_token",
        )
        _log_auth_failure(request, failure)
        raise failure
    return staff


def get_session(request: Request) -> Iterator[Session]:
    factory = getattr(request.app.state, "session_factory", None)
    if factory is None:
        raise problem("DEPENDENCY_UNAVAILABLE")
    session = factory()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def request_uuid(request: Request) -> uuid.UUID:
    return uuid.UUID(request_id_from(request))
