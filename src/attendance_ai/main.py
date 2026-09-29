"""FastAPI application factory.

Run with:  uv run uvicorn attendance_ai.main:create_app --factory --reload
"""

import logging
import re
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from attendance_ai import __version__
from attendance_ai.core.config import PROJECT_ROOT, Settings, get_settings
from attendance_ai.core.directory import Directory
from attendance_ai.core.errors import install_error_handlers
from attendance_ai.core.logging import configure_logging, request_id_var
from attendance_ai.exports.service import ExportService
from attendance_ai.feedback.examples import ExampleStore
from attendance_ai.feedback.service import FeedbackService
from attendance_ai.gateway.routes import auth, exports, feedback, health, ingestions, query, records
from attendance_ai.generation.openai_compatible import OpenAICompatibleProvider
from attendance_ai.generation.router import CircuitBreaker, LLMRouter
from attendance_ai.governance.audit import AuditLog, AuditSink
from attendance_ai.ingestion.queue import DramatiqJobQueue, JobQueue
from attendance_ai.ingestion.service import IngestionService
from attendance_ai.orchestration.answer import AnswerService
from attendance_ai.retrieval.documents import DocumentSearch
from attendance_ai.retrieval.sql_runner import SqlRunner
from attendance_ai.stores.cache import AnswerCache
from attendance_ai.stores.db import Database
from attendance_ai.stores.embeddings import Embedder
from attendance_ai.stores.file_store import FileStore
from attendance_ai.stores.queue import create_broker
from attendance_ai.stores.redis_client import create_redis
from attendance_ai.stores.vector_store import VectorStore

access_logger = logging.getLogger("attendance_ai.access")

UI_DIST = PROJECT_ROOT / "frontend" / "dist"
_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
_UI_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
    "connect-src 'self'; font-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
)


def build_router(settings: Settings, redis_client: Any) -> LLMRouter | None:
    """The provider chain from settings (primary, then fallback), or None without an API key."""
    key = settings.llm_api_key
    if key is None or not key.get_secret_value():
        return None
    providers = [
        OpenAICompatibleProvider(
            name=settings.llm_provider,
            model=model,
            base_url=settings.llm_base_url,
            api_key=key.get_secret_value(),
            timeout_seconds=settings.llm_timeout_seconds,
        )
        for model in settings.llm_models
    ]
    return LLMRouter(providers, CircuitBreaker(redis_client))


def build_answer_service(
    settings: Settings, *, directory: Directory, database: Database, redis_client: Any, audit: AuditSink
) -> AnswerService:
    documents = None
    embedder = Embedder.from_settings(settings) if settings.qdrant_url else None
    if embedder is not None:
        documents = DocumentSearch(
            VectorStore.from_settings(settings, embedder.dense_size),
            embedder,
            candidates=settings.search_candidates,
            top_k=settings.search_top_k,
            min_score=settings.rerank_min_score,
        )
    return AnswerService(
        directory=directory,
        database=database,
        router=build_router(settings, redis_client),
        sql_runner=SqlRunner(
            database, row_cap=settings.query_row_cap, evidence_cap=settings.evidence_row_cap
        ),
        documents=documents,
        cache=AnswerCache(redis_client, settings.cache_ttl_seconds),
        audit=audit,
        max_tokens=settings.llm_max_output_tokens,
        examples=ExampleStore(database, embedder, min_similarity=settings.feedback_min_similarity),
    )


def create_app(
    settings: Settings | None = None,
    *,
    directory: Directory | None = None,
    database: Any = None,
    redis_client: Any = None,
    audit: AuditSink | None = None,
    queue: JobQueue | None = None,
    answers: AnswerService | None = None,
) -> FastAPI:
    """Build the app. Collaborators can be injected (tests pass fakes); by default they are created
    from settings. Connections are lazy, so the app starts even while a dependency is down."""
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    directory = directory or Directory.from_file(settings.seed_file)
    database = database or Database(settings)
    redis_client = redis_client or create_redis(settings)
    audit = audit or AuditLog(database)
    queue = queue or DramatiqJobQueue(create_broker(settings))
    answers = answers or build_answer_service(
        settings, directory=directory, database=database, redis_client=redis_client, audit=audit
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        database.dispose()
        redis_client.close()

    show_docs = settings.app_env != "prod"
    app = FastAPI(
        title="Attendance Intelligence API",
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs" if show_docs else None,
        redoc_url=None,
        openapi_url="/openapi.json" if show_docs else None,
    )
    app.state.settings = settings
    app.state.directory = directory
    app.state.database = database
    app.state.redis = redis_client
    app.state.audit = audit
    app.state.answers = answers
    app.state.ingestion = IngestionService(
        database=database,
        directory=directory,
        file_store=FileStore(settings.storage_dir),
        queue=queue,
        audit=audit,
        max_upload_bytes=settings.upload_max_mb * 1024 * 1024,
    )
    app.state.exports = ExportService(
        database=database, directory=directory, audit=audit, row_cap=settings.export_row_cap
    )
    app.state.feedback = FeedbackService(
        database=database,
        directory=directory,
        answers=answers,
        examples=answers.example_store,
        audit=audit,
    )

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        incoming = request.headers.get("x-request-id", "")
        request_id = incoming if _REQUEST_ID.fullmatch(incoming) else f"req_{uuid.uuid4().hex[:16]}"
        request.state.request_id = request_id
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "no-referrer"
            response.headers["X-Frame-Options"] = "DENY"
            if request.url.path.startswith("/ui"):
                response.headers["Content-Security-Policy"] = _UI_CSP
            access_logger.info(
                "request",
                extra={
                    "fields": {
                        "method": request.method,
                        "path": request.url.path,
                        "status": response.status_code,
                        "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                    }
                },
            )
            return response
        finally:
            request_id_var.reset(token)

    install_error_handlers(app)
    for router in (
        health.router,
        auth.router,
        ingestions.router,
        records.router,
        query.router,
        exports.router,
        feedback.router,
    ):
        app.include_router(router)
    if settings.auth_dev_login_enabled:
        app.include_router(auth.dev_router)

    if UI_DIST.is_dir():
        app.mount("/ui", StaticFiles(directory=UI_DIST, html=True), name="ui")

    @app.get("/", include_in_schema=False)
    def root() -> Response:
        if UI_DIST.is_dir():
            return RedirectResponse("/ui/")
        return JSONResponse(
            {
                "service": "attendance-ai",
                "docs": "/docs",
                "ui": "not built (cd frontend; npm install; npm run build)",
            }
        )

    return app
