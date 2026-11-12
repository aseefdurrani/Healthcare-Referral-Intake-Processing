"""Composition root for settings, auth, health, the database, and the workflow."""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from sqlalchemy.engine import Engine
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.adapters.aws import build_aws
from app.adapters.llm_openai import OpenAILanguageModel
from app.adapters.ocr import BotoTextract
from app.adapters.storage import S3Storage
from app.api.health import router as health_router
from app.api.me import router as me_router
from app.api.referrals import router as referral_router
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
from app.db.session import build_session_factory, open_database
from app.services.clock import Clock, SystemClock
from app.services.intake import reconcile_storing
from app.services.pipeline import recover
from app.services.runner import build_runner

UVICORN_HOST = "127.0.0.1"
UVICORN_PORT = 8000
UVICORN_WORKERS = 1


def create_app(
    settings: Settings | None = None,
    *,
    jwk_client: JwkClient | None = None,
    engine: Engine | None = None,
    storage: object | None = None,
    textract: object | None = None,
    model: object | None = None,
    clock: Clock | None = None,
    sleeper: Callable[[float], None] | None = None,
) -> FastAPI:
    resolved = settings if settings is not None else load_settings()
    if (
        resolved.app_env != AppEnv.TEST
        and resolved.s3_bucket_name
        and resolved.ai_enabled
        and not resolved.openai_api_key.get_secret_value()
    ):
        raise ValueError("OPENAI_API_KEY is required when AI_ENABLED=true")
    configure_logging(resolved)
    docs_enabled = resolved.app_env == AppEnv.LOCAL
    app_clock = clock if clock is not None else SystemClock()
    app_sleep = sleeper if sleeper is not None else time.sleep

    @asynccontextmanager
    async def lifespan(running: FastAPI) -> AsyncIterator[None]:
        configure_logging(resolved)
        if resolved.allowed_staff_sub == "":
            log_event(logging.WARNING, "auth.forbidden", reason="allow_list_empty")
        opened: Engine | None = None
        if engine is not None:
            running.state.engine = engine
            running.state.session_factory = build_session_factory(engine)
        elif resolved.app_env != AppEnv.TEST:
            opened = open_database(resolved)
            running.state.engine = opened
            running.state.session_factory = build_session_factory(opened)
        else:
            running.state.session_factory = None
        store = storage
        reader = textract
        language_model = model
        if store is None and resolved.s3_bucket_name and resolved.app_env != AppEnv.TEST:
            clients = build_aws(resolved)
            store = S3Storage(clients.s3, resolved.s3_bucket_name)
            reader = BotoTextract(clients.textract, resolved)
            if resolved.ai_enabled:
                from openai import OpenAI

                client = OpenAI(
                    api_key=resolved.openai_api_key.get_secret_value(),
                    max_retries=0,
                )
                language_model = OpenAILanguageModel(client, resolved)
            else:
                language_model = OpenAILanguageModel(None, resolved)
        running.state.storage = store
        running.state.textract = reader
        running.state.model = language_model
        running.state.clock = app_clock
        runner = None
        factory = running.state.session_factory
        if (
            factory is not None
            and store is not None
            and reader is not None
            and language_model is not None
        ):
            runner = build_runner(
                factory,
                storage=store,
                textract=reader,
                model=language_model,
                settings=resolved,
                clock=app_clock,
                sleep=app_sleep,
            )
            if resolved.app_env != AppEnv.TEST:
                startup = factory()
                try:
                    pending = recover(startup, app_clock)
                    reconcile_storing(
                        startup,
                        storage=store,
                        runner=runner,
                        clock=app_clock,
                    )
                finally:
                    startup.close()
                for run_id in pending:
                    runner.submit(run_id)
        running.state.runner = runner
        try:
            yield
        finally:
            if runner is not None:
                runner.shutdown()
            if opened is not None:
                opened.dispose()

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
    app.state.clock = app_clock
    app.state.runner = None
    app.state.storage = storage
    app.include_router(health_router)
    app.include_router(me_router)
    app.include_router(referral_router)
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
