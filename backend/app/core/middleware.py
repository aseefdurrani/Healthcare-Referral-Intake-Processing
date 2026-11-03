"""Request ids and the completion log. Health checks are not logged."""

from __future__ import annotations

import logging
import re
import time
import uuid
from collections.abc import MutableMapping

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.logging import log_event, request_id_var, route_template

_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def resolve_request_id(raw: str | None) -> str:
    if raw is not None and _UUID.fullmatch(raw):
        return raw
    return str(uuid.uuid4())


def _header(scope: Scope, name: bytes) -> str | None:
    for key, value in scope.get("headers", []):
        if key.lower() == name:
            return value.decode("latin-1")
    return None


class RequestContextMiddleware:
    """Assign ``X-Request-ID``, then log one completion line for every route except health."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = resolve_request_id(_header(scope, b"x-request-id"))
        token = request_id_var.set(request_id)
        state = scope.setdefault("state", {})
        if isinstance(state, MutableMapping):
            state["request_id"] = request_id
        status_code = 500
        started = time.perf_counter()

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
                headers = MutableHeaders(scope=message)
                if "x-request-id" not in headers:
                    headers["X-Request-ID"] = request_id
                if "x-content-type-options" not in headers:
                    headers["X-Content-Type-Options"] = "nosniff"
                path = scope.get("path", "")
                if isinstance(path, str) and path.startswith("/api/"):
                    headers["Cache-Control"] = "no-store"
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            try:
                path = scope.get("path")
                if path != "/healthz":
                    duration_ms = max(0, int((time.perf_counter() - started) * 1000))
                    fields: dict[str, object] = {
                        "method": scope.get("method", ""),
                        "route": route_template(scope),
                        "status": status_code,
                        "duration_ms": duration_ms,
                    }
                    state_now = scope.get("state", {})
                    actor_id = None
                    if isinstance(state_now, MutableMapping):
                        actor_id = state_now.get("actor_id")
                    if isinstance(actor_id, str):
                        fields["actor_id"] = actor_id
                    level = logging.ERROR if status_code >= 500 else logging.INFO
                    log_event(level, "http.request.completed", **fields)
            finally:
                request_id_var.reset(token)
