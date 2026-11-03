"""Composition root for settings, auth, and health. No AWS or database clients."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.health import router as health_router
from app.api.me import router as me_router
from app.core.auth import JwkClient, TokenVerifier, build_jwk_client
from app.core.config import AppEnv, Settings, load_settings
from app.core.errors import (
    AppError,
    app_error_handler,
    http_exception_handler,
    unhandled_error_handler,
    validation_error_handler,
)
from app.core.logging import configure_logging, log_event
from app.core.middleware import RequestContextMiddleware

UVICORN_HOST = "127.0.0.1"
UVICORN_PORT = 8000
UVICORN_WORKERS = 1


def create_app(
    settings: Settings | None = None,
    *,
    jwk_client: JwkClient | None = None,
) -> FastAPI:
    resolved = settings if settings is not None else load_settings()
    configure_logging(resolved)
    docs_enabled = resolved.app_env == AppEnv.LOCAL

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        configure_logging(resolved)
        if resolved.allowed_staff_sub == "":
            log_event(logging.WARNING, "auth.forbidden", reason="allow_list_empty")
        yield

    app = FastAPI(
        title="Cortrucent Intake API",
        version=resolved.app_version,
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
        openapi_url="/openapi.json" if docs_enabled else None,
        lifespan=lifespan,
        debug=False,
    )
    app.state.settings = resolved
    app.state.verifier = TokenVerifier(resolved, jwk_client or build_jwk_client(resolved))
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)
    app.add_middleware(RequestContextMiddleware)
    app.include_router(health_router)
    app.include_router(me_router)
    return app


app = create_app()


def serve() -> None:
    """Bind one worker to loopback and disable Uvicorn's access log."""

    resolved = load_settings()
    configure_logging(resolved)
    uvicorn.run(
        "app.main:app",
        host=UVICORN_HOST,
        port=UVICORN_PORT,
        workers=UVICORN_WORKERS,
        access_log=False,
    )
