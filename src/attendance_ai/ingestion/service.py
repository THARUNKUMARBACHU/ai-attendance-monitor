"""The API side of ingestion: check an upload, detect duplicates and new versions, store the original,
create the job and queue it. Processing happens in the worker (ingestion/pipeline.py), or in the upload
request itself in inline mode (ingestion/inline.py)."""

from __future__ import annotations

import hashlib
import logging
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from psycopg.errors import UniqueViolation
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from attendance_ai.core.access import AccessContext
from attendance_ai.core.directory import Directory
from attendance_ai.core.errors import AppError, NotFoundError
from attendance_ai.governance.audit import AuditEvent, AuditSink
from attendance_ai.ingestion.file_checks import FileRejectedError, inspect_upload, safe_filename
from attendance_ai.ingestion.queue import MAX_ATTEMPTS, JobMessage, JobQueue
from attendance_ai.stores.db import Database
from attendance_ai.stores.file_store import FileStore
from attendance_ai.stores.models import IngestionJob, Source, SourceVersion

logger = logging.getLogger(__name__)

STAGES = ("validate", "extract", "normalise", "store", "index")
INTERRUPTED_MESSAGE = "Processing was interrupted before it finished. Upload the file again to retry."


class UploadConflictError(AppError):
    status_code = 409
    code = "upload_conflict"
    title = "Upload conflict"


@dataclass(frozen=True, slots=True)
class Receipt:
    job_id: str
    status: str
    filename: str
    source_id: str
    version_no: int
    checksum: str
    media_type: str
    size_bytes: int
    duplicate_of_job_id: str | None


def _now() -> datetime:
    return datetime.now(UTC)


def initial_stages(detail: str) -> list[dict[str, Any]]:
    now = _now().isoformat()
    stages: list[dict[str, Any]] = [
        {"name": name, "status": "pending", "started_at": None, "finished_at": None, "detail": None}
        for name in STAGES
    ]
    stages[0].update(status="done", started_at=now, finished_at=now, detail=detail)
    return stages


class IngestionService:
    def __init__(
        self,
        *,
        database: Database,
        directory: Directory,
        file_store: FileStore,
        queue: JobQueue,
        audit: AuditSink,
        max_upload_bytes: int,
        interrupted_after: timedelta | None = None,
    ) -> None:
        """`interrupted_after` is for inline mode, where no worker sweeps up stalled jobs: a job still
        queued or running after that long was cut short with its request, and is shown as failed."""
        self._db = database
        self._directory = directory
        self._files = file_store
        self._queue = queue
        self._audit = audit
        self._max_bytes = max_upload_bytes
        self._interrupted_after = interrupted_after

    def submit(self, ctx: AccessContext, *, filename: str, data: bytes, entity_id: str | None) -> Receipt:
        ctx.require("ingest")
        tenant = self._directory.get_tenant(ctx.tenant_id)
        if entity_id and (tenant is None or entity_id not in tenant.departments):
            raise FileRejectedError(f"Unknown department '{entity_id}'.")
        inspected = inspect_upload(filename, data, max_bytes=self._max_bytes)
        name = safe_filename(filename, inspected.extension)
        checksum = "sha256:" + hashlib.sha256(data).hexdigest()
        try:
            receipt, message = self._record(
                ctx, name, data, checksum, inspected.media_type, inspected.extension, entity_id
            )
        except IntegrityError as exc:
            # Only a unique-key clash means two uploads of the same file raced each other; any other
            # integrity error is a bug and must not be disguised as a conflict.
            if not isinstance(exc.orig, UniqueViolation):
                raise
            raise UploadConflictError(
                "The same file is being uploaded right now. Try again shortly."
            ) from exc
        if message is not None:
            try:
                self._queue.enqueue(message)
            except Exception:
                # The job stays queued in PostgreSQL; the worker's sweep picks it up once the queue is back.
                logger.exception("enqueue_failed", extra={"fields": {"job_id": message.job_id}})
        self._audit.record(
            AuditEvent(
                "ingest",
                "upload_duplicate" if receipt.status == "duplicate" else "upload_accepted",
                "success",
                details={"filename": name, "job_id": receipt.job_id, "checksum": checksum, "size": len(data)},
            ),
            ctx,
        )
        return receipt

    def _record(
        self,
        ctx: AccessContext,
        name: str,
        data: bytes,
        checksum: str,
        media_type: str,
        extension: str,
        entity_id: str | None,
    ) -> tuple[Receipt, JobMessage | None]:
        with self._db.session_for(ctx) as session:
            duplicate = session.scalars(
                select(SourceVersion).where(SourceVersion.checksum == checksum)
            ).first()
            if duplicate is not None and duplicate.status == "failed":
                return self._retry_failed(session, ctx, duplicate, data, checksum, extension, entity_id)
            if duplicate is not None:
                original = session.scalars(
                    select(IngestionJob)
                    .where(IngestionJob.source_version_id == duplicate.id, IngestionJob.status != "duplicate")
                    .order_by(IngestionJob.created_at)
                ).first()
                job = IngestionJob(
                    tenant_id=ctx.tenant_id,
                    product_id=ctx.product_id,
                    module=ctx.module,
                    source_version_id=duplicate.id,
                    requested_by=ctx.user_id,
                    status="duplicate",
                    stages=initial_stages("identical to an earlier upload; nothing to process"),
                    finished_at=_now(),
                )
                session.add(job)
                session.flush()
                receipt = Receipt(
                    job_id=str(job.id),
                    status="duplicate",
                    filename=duplicate.original_filename,
                    source_id=str(duplicate.source_id),
                    version_no=duplicate.version_no,
                    checksum=checksum,
                    media_type=duplicate.media_type,
                    size_bytes=duplicate.size_bytes,
                    duplicate_of_job_id=str(original.id) if original else None,
                )
                return receipt, None

            source = session.scalars(select(Source).where(Source.logical_name == name)).first()
            if source is None:
                source = Source(
                    tenant_id=ctx.tenant_id,
                    product_id=ctx.product_id,
                    module=ctx.module,
                    entity_id=entity_id,
                    logical_name=name,
                    created_by=ctx.user_id,
                )
                session.add(source)
                session.flush()
            elif entity_id and source.entity_id != entity_id:
                source.entity_id = entity_id
            latest = session.scalar(
                select(func.max(SourceVersion.version_no)).where(SourceVersion.source_id == source.id)
            )
            version_id = uuid.uuid4()
            stored = self._files.save(ctx.tenant_id, f"{version_id}{extension}", data)
            version = SourceVersion(
                id=version_id,
                source_id=source.id,
                tenant_id=ctx.tenant_id,
                product_id=ctx.product_id,
                module=ctx.module,
                version_no=(latest or 0) + 1,
                checksum=checksum,
                media_type=media_type,
                size_bytes=len(data),
                storage_path=stored,
                original_filename=name,
                created_by=ctx.user_id,
            )
            # The models have no relationship() between them, so the ORM does not order these inserts by
            # their foreign key: the version must be written before the job that points to it.
            session.add(version)
            session.flush()
            job = IngestionJob(
                tenant_id=ctx.tenant_id,
                product_id=ctx.product_id,
                module=ctx.module,
                source_version_id=version_id,
                requested_by=ctx.user_id,
                status="queued",
                max_attempts=MAX_ATTEMPTS,
                stages=initial_stages(f"{media_type}, {len(data)} bytes"),
            )
            session.add(job)
            session.flush()
            receipt = Receipt(
                job_id=str(job.id),
                status="queued",
                filename=name,
                source_id=str(source.id),
                version_no=version.version_no,
                checksum=checksum,
                media_type=media_type,
                size_bytes=len(data),
                duplicate_of_job_id=None,
            )
            message = JobMessage(str(job.id), ctx.tenant_id, ctx.product_id, ctx.module)
            return receipt, message

    def _retry_failed(
        self,
        session: Any,
        ctx: AccessContext,
        version: SourceVersion,
        data: bytes,
        checksum: str,
        extension: str,
        entity_id: str | None,
    ) -> tuple[Receipt, JobMessage]:
        """The same file was uploaded before and its processing failed: process that version again with a
        new job. The file is stored again, because hosts without a persistent disk may have lost it."""
        version.storage_path = self._files.save(ctx.tenant_id, f"{version.id}{extension}", data)
        version.status = "received"
        source = session.get(Source, version.source_id)
        if source is not None and entity_id and source.entity_id != entity_id:
            source.entity_id = entity_id
        job = IngestionJob(
            tenant_id=ctx.tenant_id,
            product_id=ctx.product_id,
            module=ctx.module,
            source_version_id=version.id,
            requested_by=ctx.user_id,
            status="queued",
            max_attempts=MAX_ATTEMPTS,
            stages=initial_stages(f"retry after a failed attempt; {version.media_type}, {len(data)} bytes"),
        )
        session.add(job)
        session.flush()
        receipt = Receipt(
            job_id=str(job.id),
            status="queued",
            filename=version.original_filename,
            source_id=str(version.source_id),
            version_no=version.version_no,
            checksum=checksum,
            media_type=version.media_type,
            size_bytes=version.size_bytes,
            duplicate_of_job_id=None,
        )
        return receipt, JobMessage(str(job.id), ctx.tenant_id, ctx.product_id, ctx.module)

    def list_jobs(self, ctx: AccessContext, *, limit: int) -> list[dict[str, Any]]:
        ctx.require("ingest")
        with self._db.session_for(ctx) as session:
            rows = session.execute(
                select(IngestionJob, SourceVersion, Source)
                .join(SourceVersion, IngestionJob.source_version_id == SourceVersion.id)
                .join(Source, SourceVersion.source_id == Source.id)
                .order_by(IngestionJob.created_at.desc())
                .limit(limit)
            ).all()
            closed = self._close_interrupted(session, [(job, version) for job, version, _ in rows])
            summaries = [job_summary(job, version, source) for job, version, source in rows]
        self._audit_interrupted(ctx, closed)
        return summaries

    def get_job(self, ctx: AccessContext, job_id: str) -> dict[str, Any]:
        ctx.require("ingest")
        try:
            key = uuid.UUID(job_id)
        except ValueError:
            raise NotFoundError("No such job.") from None
        with self._db.session_for(ctx) as session:
            row = session.execute(
                select(IngestionJob, SourceVersion, Source)
                .join(SourceVersion, IngestionJob.source_version_id == SourceVersion.id)
                .join(Source, SourceVersion.source_id == Source.id)
                .where(IngestionJob.id == key)
            ).one_or_none()
            if row is None:
                raise NotFoundError("No such job.")
            job, version, source = row
            closed = self._close_interrupted(session, [(job, version)])
            detail = job_detail(job, version, source)
        self._audit_interrupted(ctx, closed)
        return detail

    def _close_interrupted(
        self, session: Any, jobs: Iterable[tuple[IngestionJob, SourceVersion]]
    ) -> list[str]:
        """Mark jobs that were cut short as failed (inline mode only); returns their IDs."""
        if self._interrupted_after is None:
            return []
        cutoff = _now() - self._interrupted_after
        closed: list[str] = []
        for job, version in jobs:
            if job.status not in ("queued", "running") or job.updated_at >= cutoff:
                continue
            stages = [dict(stage) for stage in job.stages]
            for stage in stages:
                if stage["status"] == "running":
                    stage["status"] = "failed"
                    stage["detail"] = INTERRUPTED_MESSAGE
            job.stages = stages
            job.status = "failed"
            job.last_error = INTERRUPTED_MESSAGE
            job.finished_at = job.updated_at = _now()
            version.status = "failed"
            closed.append(str(job.id))
        return closed

    def _audit_interrupted(self, ctx: AccessContext, job_ids: list[str]) -> None:
        for job_id in job_ids:
            logger.warning("job_interrupted", extra={"fields": {"job_id": job_id}})
            self._audit.record(
                AuditEvent(
                    "ingest", "job_failed", "failed", details={"job_id": job_id, "error": "interrupted"}
                ),
                ctx,
            )


def job_summary(job: IngestionJob, version: SourceVersion, source: Source) -> dict[str, Any]:
    return {
        "job_id": str(job.id),
        "filename": version.original_filename,
        "version_no": version.version_no,
        "status": job.status,
        "created_at": job.created_at.isoformat(),
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "counts": job.counts,
    }


def job_detail(job: IngestionJob, version: SourceVersion, source: Source) -> dict[str, Any]:
    return {
        **job_summary(job, version, source),
        "stages": job.stages,
        "attempts": job.attempts,
        "max_attempts": job.max_attempts,
        "failures": job.failures,
        "last_error": job.last_error,
        "checksum": version.checksum,
        "media_type": version.media_type,
        "size_bytes": version.size_bytes,
        "entity_id": source.entity_id,
        "started_at": job.started_at.isoformat() if job.started_at else None,
    }
