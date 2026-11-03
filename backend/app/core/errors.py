"""RFC 9457 responses. Bodies carry a code and correlation id, never request values."""

from __future__ import annotations

import logging
import re
import traceback
import uuid
from collections.abc import Mapping
from pathlib import Path

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import log_event, request_id_var, route_template

_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_FRAME = re.compile(r"^app/[\w./-]+:\d+ in [A-Za-z_][A-Za-z0-9_]*$")

_CATALOG: dict[str, tuple[int, str, str, dict[str, str] | None]] = {
    "AUTH_REQUIRED": (
        401,
        "Authentication required",
        "Provide a Bearer access token.",
        {"WWW-Authenticate": "Bearer"},
    ),
    "TOKEN_INVALID": (
        401,
        "Invalid access token",
        "The access token could not be verified.",
        {"WWW-Authenticate": "Bearer"},
    ),
    "TOKEN_EXPIRED": (
        401,
        "Access token expired",
        "Sign in again.",
        {"WWW-Authenticate": "Bearer"},
    ),
    "NOT_AUTHORIZED": (
        403,
        "Not authorized",
        "This account cannot use the intake API.",
        None,
    ),
    "AUTH_UNAVAILABLE": (
        503,
        "Sign-in service unavailable",
        "The token key set could not be fetched.",
        None,
    ),
    "NOT_FOUND": (404, "Not found", "No resource matches this path.", None),
    "METHOD_NOT_ALLOWED": (
        405,
        "Method not allowed",
        "This method is not supported on this path.",
        None,
    ),
    "VALIDATION_ERROR": (
        422,
        "Request could not be validated",
        "The request body or query is invalid.",
        None,
    ),
    "INTERNAL_ERROR": (500, "Internal error", "The request could not be completed.", None),
}


class AppError(Exception):
    """A problem response the API chooses. The message is the code, not caller data."""

    def __init__(
        self,
        *,
        status: int,
        code: str,
        title: str,
        detail: str,
        headers: dict[str, str] | None = None,
        reason: str | None = None,
        errors: list[dict[str, object]] | None = None,
        actor_id: str | None = None,
        exception_type: str | None = None,
    ) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        self.title = title
        self.detail = detail
        self.headers = headers or {}
        self.reason = reason
        self.errors = errors
        self.actor_id = actor_id
        self.exception_type = exception_type


def problem(
    code: str,
    *,
    reason: str | None = None,
    actor_id: str | None = None,
    exception_type: str | None = None,
    errors: list[dict[str, object]] | None = None,
) -> AppError:
    status, title, detail, headers = _CATALOG[code]
    return AppError(
        status=status,
        code=code,
        title=title,
        detail=detail,
        headers=headers,
        reason=reason,
        actor_id=actor_id,
        exception_type=exception_type,
        errors=errors,
    )


def request_id_from(request: Request) -> str:
    state_value = getattr(request.state, "request_id", None)
    if isinstance(state_value, str) and _UUID.fullmatch(state_value):
        return state_value
    context_value = request_id_var.get()
    if isinstance(context_value, str) and _UUID.fullmatch(context_value):
        return context_value
    return str(uuid.uuid4())


def problem_response(
    request: Request,
    *,
    status: int,
    code: str,
    title: str,
    detail: str,
    headers: Mapping[str, str] | None = None,
    errors: list[dict[str, object]] | None = None,
) -> JSONResponse:
    body: dict[str, object] = {
        "type": f"urn:cortrucent:problem:{code.lower().replace('_', '-')}",
        "title": title,
        "status": status,
        "detail": detail,
        "code": code,
        "correlation_id": request_id_from(request),
    }
    if errors:
        body["errors"] = errors
    response_headers = {
        "X-Request-ID": str(body["correlation_id"]),
        "X-Content-Type-Options": "nosniff",
    }
    response_headers.update(headers or {})
    return JSONResponse(
        status_code=status,
        content=body,
        media_type="application/problem+json",
        headers=response_headers,
    )


def safe_stack(exc: BaseException) -> list[str]:
    """File, line, and function only, and only for frames inside ``app``."""

    frames: list[str] = []
    for frame in traceback.extract_tb(exc.__traceback__):
        parts = Path(frame.filename).parts
        if "app" not in parts:
            continue
        relative = "/".join(parts[parts.index("app") :])
        rendered = f"{relative}:{frame.lineno} in {frame.name}"
        if _FRAME.fullmatch(rendered):
            frames.append(rendered)
    return frames[-8:]


def _public_errors(exc: RequestValidationError) -> list[dict[str, object]]:
    public: list[dict[str, object]] = []
    for err in exc.errors():
        loc = err.get("loc", ())
        msg = err.get("msg", "Invalid value")
        raw = err.get("input")
        if not isinstance(msg, str) or (isinstance(raw, str) and raw and raw in msg):
            msg = "Invalid value"
        public.append(
            {
                "loc": [part if isinstance(part, int) else str(part) for part in loc],
                "msg": msg,
            }
        )
    return public


async def app_error_handler(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, AppError):
        raise exc
    return problem_response(
        request,
        status=exc.status,
        code=exc.code,
        title=exc.title,
        detail=exc.detail,
        headers=exc.headers,
        errors=exc.errors,
    )


async def validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, RequestValidationError):
        raise exc
    status, title, detail, headers = _CATALOG["VALIDATION_ERROR"]
    return problem_response(
        request,
        status=status,
        code="VALIDATION_ERROR",
        title=title,
        detail=detail,
        headers=headers,
        errors=_public_errors(exc),
    )


async def http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, StarletteHTTPException):
        raise exc
    if exc.status_code == 404:
        code = "NOT_FOUND"
    elif exc.status_code == 405:
        code = "METHOD_NOT_ALLOWED"
    elif exc.status_code >= 500:
        code = "INTERNAL_ERROR"
    else:
        return problem_response(
            request,
            status=exc.status_code,
            code="NOT_FOUND",
            title="Not found",
            detail="The request was rejected.",
        )
    status, title, detail, headers = _CATALOG[code]
    return problem_response(
        request,
        status=status,
        code=code,
        title=title,
        detail=detail,
        headers=headers,
    )


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    request_id = request_id_from(request)
    token = request_id_var.set(request_id)
    try:
        log_event(
            logging.ERROR,
            "app.error.unhandled",
            exception_type=type(exc).__name__,
            error_code="INTERNAL_ERROR",
            route=route_template(request.scope),
            stack=safe_stack(exc),
        )
    finally:
        request_id_var.reset(token)
    status, title, detail, headers = _CATALOG["INTERNAL_ERROR"]
    return problem_response(
        request,
        status=status,
        code="INTERNAL_ERROR",
        title=title,
        detail=detail,
        headers=headers,
    )
