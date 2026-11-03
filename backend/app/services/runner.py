"""In-process runner. One process, two workers, and a fresh session per run."""

from __future__ import annotations

import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from threading import Lock
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from app.services.pipeline import Pipeline


class ProcessingRunner:
    def __init__(
        self, session_factory: sessionmaker[Session], pipeline: Pipeline, *, max_workers: int
    ) -> None:
        self._factory = session_factory
        self._pipeline = pipeline
        self.max_workers = max_workers
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="intake")
        self._inflight: set[uuid.UUID] = set()
        self._futures: list[Future[None]] = []
        self._lock = Lock()

    def submit(self, run_id: uuid.UUID) -> None:
        with self._lock:
            if run_id in self._inflight:
                return
            self._inflight.add(run_id)
            future = self._executor.submit(self._safe, run_id)
            self._futures.append(future)
        future.add_done_callback(lambda _future: self._finish(run_id))

    def drain(self, timeout: float = 30) -> None:
        with self._lock:
            pending = list(self._futures)
        for future in pending:
            future.result(timeout=timeout)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _finish(self, run_id: uuid.UUID) -> None:
        with self._lock:
            self._inflight.discard(run_id)

    def _safe(self, run_id: uuid.UUID) -> None:
        session = self._factory()
        try:
            self._pipeline.execute(session, run_id)
        finally:
            session.close()


def build_runner(
    session_factory: sessionmaker[Session],
    *,
    storage: Any,
    textract: Any,
    model: Any,
    settings: Any,
    clock: Any,
    sleep: Any,
) -> ProcessingRunner:
    pipeline = Pipeline(
        storage=storage,
        textract=textract,
        model=model,
        settings=settings,
        clock=clock,
        sleep=sleep,
    )
    return ProcessingRunner(
        session_factory,
        pipeline,
        max_workers=settings.processing_max_workers,
    )
