"""The worker side of ingestion: run one job's stages (extract, normalise, store, index) under the
job's tenant context, recording each stage's status on the job.

Transient failures (database, network, vector store) are retried by the queue with backoff, up to the
job's attempt limit. Anything else fails the job at once with a readable error.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from attendance_ai.core.access import AccessContext
from attendance_ai.core.config import Settings
from attendance_ai.core.directory import Directory
from attendance_ai.governance.audit import AuditEvent, AuditSink
from attendance_ai.ingestion.chunking import narrative_chunks, remark_chunks
from attendance_ai.ingestion.indexing import DocumentIndexer
from attendance_ai.ingestion.normalize import Normaliser
from attendance_ai.ingestion.parsers import parse_file
from attendance_ai.ingestion.store import store_records
from attendance_ai.ingestion.types import ParseResult
from attendance_ai.stores.db import Database
from attendance_ai.stores.file_store import FileStore
from attendance_ai.stores.models import AttendanceRecord, IngestionJob, Source, SourceVersion
from attendance_ai.stores.versions import bump_data_version

logger = logging.getLogger(__name__)

MAX_REPORTED_FAILURES = 500
TRANSIENT_ERRORS: tuple[type[BaseException], ...] = (
    OperationalError,
    ConnectionError,
    TimeoutError,
    httpx.TransportError,
    ResponseHandlingException,
    UnexpectedResponse,
)


class RetryLaterError(RuntimeError):
    """Raised to let the queue retry the job after a backoff."""


@dataclass(frozen=True, slots=True)
class ClaimedJob:
    job_id: uuid.UUID
    attempts: int
    max_attempts: int
    source_id: uuid.UUID
    version_id: uuid.UUID
    storage_path: str
    media_type: str
    filename: str
    entity_id: str | None
    classification: str


def _now() -> datetime:
    return datetime.now(UTC)


class IngestionPipeline:
    def __init__(
        self,
        *,
        settings: Settings,
        directory: Directory,
        database: Database,
        file_store: FileStore,
        indexer: DocumentIndexer | None,
        audit: AuditSink,
    ) -> None:
        self._settings = settings
        self._directory = directory
        self._db = database
        self._files = file_store
        self._indexer = indexer
        self._audit = audit

    def run(self, job_id: str, tenant_id: str, product_id: str, module: str) -> None:
        ctx = self._directory.system_context(
            tenant_id=tenant_id, product_id=product_id, module=module, request_id=f"job_{job_id[:18]}"
        )
        claimed = self._claim(ctx, uuid.UUID(job_id))
        if claimed is None:
            return
        try:
            self._process(ctx, claimed)
        except Exception as exc:
            self._fail(ctx, claimed, exc)

    def requeue_stale(self, ctx: AccessContext, *, older_than: timedelta) -> list[str]:
        """Job IDs left queued (for example, enqueueing failed after commit) or stuck running."""
        cutoff = _now() - older_than
        with self._db.session_for(ctx) as session:
            jobs = session.scalars(
                select(IngestionJob).where(
                    IngestionJob.status.in_(["queued", "running"]), IngestionJob.updated_at < cutoff
                )
            ).all()
            for job in jobs:
                job.status = "queued"
                job.updated_at = _now()
            return [str(job.id) for job in jobs]

    def _claim(self, ctx: AccessContext, job_id: uuid.UUID) -> ClaimedJob | None:
        with self._db.session_for(ctx) as session:
            job = session.execute(
                select(IngestionJob).where(IngestionJob.id == job_id).with_for_update()
            ).scalar_one_or_none()
            if job is None or job.status != "queued" or job.source_version_id is None:
                return None  # already handled, being handled, or not for this tenant
            version = session.get(SourceVersion, job.source_version_id)
            source = session.get(Source, version.source_id) if version else None
            if version is None or source is None:
                return None
            job.status = "running"
            job.attempts += 1
            job.started_at = job.started_at or _now()
            job.updated_at = _now()
            job.last_error = None
            version.status = "processing"
            return ClaimedJob(
                job_id=job.id,
                attempts=job.attempts,
                max_attempts=job.max_attempts,
                source_id=source.id,
                version_id=version.id,
                storage_path=version.storage_path,
                media_type=version.media_type,
                filename=version.original_filename,
                entity_id=source.entity_id,
                classification=source.classification,
            )

    def _process(self, ctx: AccessContext, job: ClaimedJob) -> None:
        tenant = self._directory.get_tenant(ctx.tenant_id)
        if tenant is None:
            raise ValueError(f"Unknown tenant {ctx.tenant_id}")

        self._stage(ctx, job, "extract", "running")
        parsed: ParseResult = parse_file(
            self._files.path_for(ctx.tenant_id, job.storage_path),
            job.media_type,
            tesseract_cmd=self._settings.tesseract_cmd,
        )
        self._stage(
            ctx,
            job,
            "extract",
            "done",
            detail=f"{len(parsed.rows)} rows, {len(parsed.texts)} text blocks",
            counts={"rows_read": len(parsed.rows)},
        )

        self._stage(ctx, job, "normalise", "running")
        batch = Normaliser(ctx, tenant, classification=job.classification).run(parsed.rows)
        self._stage(
            ctx,
            job,
            "normalise",
            "done",
            detail=f"{len(batch.records)} records, {len(batch.failures)} rejected",
            counts={"needs_review": batch.needs_review},
        )

        self._stage(ctx, job, "store", "running")
        with self._db.session_for(ctx) as session:
            outcome = store_records(
                session,
                source_id=job.source_id,
                version_id=job.version_id,
                source_file=job.filename,
                records=batch.records,
            )
            self._switch_version(session, job)
            bump_data_version(session, ctx)
            active = list(
                session.scalars(
                    select(AttendanceRecord).where(
                        AttendanceRecord.source_id == job.source_id, AttendanceRecord.is_active
                    )
                )
            )
        failures = [*batch.failures, *outcome.failures]
        self._stage(
            ctx,
            job,
            "store",
            "done",
            detail=(
                f"{outcome.created} created, {outcome.updated} updated, "
                f"{outcome.unchanged} unchanged, {outcome.removed} removed"
            ),
            counts={
                "records_created": outcome.created,
                "records_updated": outcome.updated,
                "records_unchanged": outcome.unchanged,
                "records_removed": outcome.removed,
                "rejected": len(failures),
            },
        )

        index_counts = self._index(ctx, job, active, parsed)
        status = "completed_with_errors" if failures else "completed"
        with self._db.session_for(ctx) as session:
            row = session.get(IngestionJob, job.job_id)
            if row is not None:
                row.status = status
                row.finished_at = _now()
                row.updated_at = _now()
                row.failures = [
                    {"location": failure.location, "reason": failure.reason}
                    for failure in failures[:MAX_REPORTED_FAILURES]
                ]
                row.counts = {**row.counts, **index_counts}
        self._audit.record(
            AuditEvent(
                "ingest",
                "job_completed",
                status,
                details={"job_id": str(job.job_id), "filename": job.filename, "rejected": len(failures)},
            ),
            ctx,
        )

    def _index(
        self, ctx: AccessContext, job: ClaimedJob, active: Sequence[AttendanceRecord], parsed: ParseResult
    ) -> dict[str, int]:
        if self._indexer is None:
            self._stage(ctx, job, "index", "skipped", detail="vector store not configured")
            return {"chunks_indexed": 0, "chunks_quarantined": 0}
        self._stage(ctx, job, "index", "running")
        chunks = [
            *remark_chunks(str(job.source_id), active),
            *narrative_chunks(
                str(job.source_id), parsed.texts, entity_id=job.entity_id, classification=job.classification
            ),
        ]
        outcome = self._indexer.index_source(
            ctx,
            chunks,
            source_id=str(job.source_id),
            source_version_id=str(job.version_id),
            source_file=job.filename,
        )
        for chunk in chunks:
            if chunk.quarantined:
                self._audit.record(
                    AuditEvent(
                        "security",
                        "injection_quarantined",
                        "blocked",
                        details={
                            "filename": job.filename,
                            "location": chunk.location,
                            "signals": list(chunk.injection_signals),
                        },
                    ),
                    ctx,
                )
        counts = {"chunks_indexed": outcome.indexed, "chunks_quarantined": outcome.quarantined}
        detail = f"{outcome.indexed} chunks indexed, {outcome.quarantined} quarantined"
        self._stage(ctx, job, "index", "done", detail=detail, counts=counts)
        return counts

    def _switch_version(self, session: Any, job: ClaimedJob) -> None:
        source = session.get(Source, job.source_id)
        previous = source.current_version_id if source else None
        if source is not None:
            source.current_version_id = job.version_id
        version = session.get(SourceVersion, job.version_id)
        if version is not None:
            version.status = "processed"
        if previous and previous != job.version_id:
            old = session.get(SourceVersion, previous)
            if old is not None:
                old.status = "superseded"

    def _stage(
        self,
        ctx: AccessContext,
        job: ClaimedJob,
        name: str,
        status: str,
        *,
        detail: str | None = None,
        counts: dict[str, int] | None = None,
    ) -> None:
        with self._db.session_for(ctx) as session:
            row = session.get(IngestionJob, job.job_id)
            if row is None:
                return
            stages = [dict(stage) for stage in row.stages]
            for stage in stages:
                if stage["name"] == name:
                    now = _now().isoformat()
                    if status == "running":
                        stage["started_at"] = now
                    else:
                        stage["finished_at"] = now
                        stage["started_at"] = stage["started_at"] or now
                    stage["status"] = status
                    if detail is not None:
                        stage["detail"] = detail
            row.stages = stages
            if counts:
                row.counts = {**row.counts, **counts}
            row.updated_at = _now()

    def _fail(self, ctx: AccessContext, job: ClaimedJob, exc: Exception) -> None:
        retry = isinstance(exc, TRANSIENT_ERRORS) and job.attempts < job.max_attempts
        message = f"{type(exc).__name__}: {str(exc)[:500]}"
        logger.error(
            "ingestion_failed",
            exc_info=exc,
            extra={"fields": {"job_id": str(job.job_id), "attempt": job.attempts, "will_retry": retry}},
        )
        with self._db.session_for(ctx) as session:
            row = session.get(IngestionJob, job.job_id)
            if row is not None:
                stages = [dict(stage) for stage in row.stages]
                for stage in stages:
                    if stage["status"] == "running":
                        stage["status"] = "pending" if retry else "failed"
                        stage["detail"] = message
                row.stages = stages
                row.status = "queued" if retry else "failed"
                row.last_error = message
                row.updated_at = _now()
                if not retry:
                    row.finished_at = _now()
                    version = session.get(SourceVersion, job.version_id)
                    if version is not None:
                        version.status = "failed"
        if not retry:
            self._audit.record(
                AuditEvent(
                    "ingest", "job_failed", "failed", details={"job_id": str(job.job_id), "error": message}
                ),
                ctx,
            )
            return
        raise RetryLaterError(message) from exc
