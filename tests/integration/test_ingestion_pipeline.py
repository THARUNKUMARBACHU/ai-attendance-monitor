"""Ingestion end to end with the real sample files: upload -> queue -> pipeline -> PostgreSQL + vector
store, checked against the ground truth. Jobs run through the inline queue (INGESTION_MODE=inline), so
each upload is processed before submit returns; the vector store is Qdrant's in-process mode with a
deterministic embedder, so no network or model download is needed."""

import json
import shutil
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from qdrant_client import QdrantClient
from sqlalchemy import select, update

from attendance_ai.core.access import AccessContext
from attendance_ai.core.directory import Directory
from attendance_ai.governance.audit import AuditLog
from attendance_ai.ingestion.indexing import DocumentIndexer
from attendance_ai.ingestion.inline import InlineJobQueue
from attendance_ai.ingestion.pipeline import IngestionPipeline
from attendance_ai.ingestion.queue import JobMessage, JobQueue
from attendance_ai.ingestion.service import INTERRUPTED_MESSAGE, IngestionService
from attendance_ai.stores.db import Database
from attendance_ai.stores.file_store import FileStore
from attendance_ai.stores.models import AttendanceRecord, IngestionJob
from attendance_ai.stores.vector_store import VectorStore

from ..fakes import FakeEmbedder
from ..support import PROJECT_ROOT, find_tesseract, make_settings

SAMPLES = PROJECT_ROOT / "sample_data" / "tenants"
TESSERACT = find_tesseract()
Ctx = Callable[[str], AccessContext]


def _inline_queue(database: Database, directory: Directory, files: FileStore) -> InlineJobQueue:
    pipeline = IngestionPipeline(
        settings=make_settings(tesseract_cmd=TESSERACT),
        directory=directory,
        database=database,
        file_store=files,
        indexer=DocumentIndexer(
            VectorStore(QdrantClient(location=":memory:"), "chunks", FakeEmbedder.dense_size),
            FakeEmbedder(),  # type: ignore[arg-type]
        ),
        audit=AuditLog(database),
    )
    return InlineJobQueue(pipeline.run, pause_seconds=0)


def _service(
    database: Database,
    directory: Directory,
    files: FileStore,
    queue: JobQueue,
    *,
    interrupted_after: timedelta | None = timedelta(minutes=10),
) -> IngestionService:
    return IngestionService(
        database=database,
        directory=directory,
        file_store=files,
        queue=queue,
        audit=AuditLog(database),
        max_upload_bytes=20 * 1024 * 1024,
        interrupted_after=interrupted_after,
    )


@pytest.fixture
def ingest(database: Database, directory: Directory, tmp_path: Path) -> Callable[..., dict[str, Any]]:
    files = FileStore(tmp_path)
    service = _service(database, directory, files, _inline_queue(database, directory, files))

    def _ingest(
        ctx: AccessContext, path: Path, *, name: str | None = None, entity: str | None = None
    ) -> dict[str, Any]:
        receipt = service.submit(ctx, filename=name or path.name, data=path.read_bytes(), entity_id=entity)
        detail = service.get_job(ctx, receipt.job_id)
        return {"receipt": receipt, "job": detail}

    return _ingest


def _ground_truth(tenant: str) -> list[dict[str, Any]]:
    path = SAMPLES / tenant / "ground_truth" / "canonical_records.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _unique(path: Path, tmp_path: Path, suffix: str) -> Path:
    """A copy with a unique name and content, so repeated test runs don't look like duplicates."""
    target = tmp_path / f"{path.stem}_{suffix}{path.suffix}"
    shutil.copyfile(path, target)
    return target


def _records(database: Database, ctx: AccessContext, source_file: str) -> list[AttendanceRecord]:
    with database.session_for(ctx) as session:
        return list(
            session.scalars(
                select(AttendanceRecord).where(
                    AttendanceRecord.source_file == source_file, AttendanceRecord.is_active
                )
            )
        )


NATIVE = [
    ("acme", "acme_engineering_biometric_2026-08.csv"),
    ("acme", "acme_sales_muster_2026-08.xlsx"),
    ("acme", "acme_operations_weekly_report_2026-08.docx"),
    ("acme", "acme_finance_attendance_2026-08.pdf"),
    ("globex", "globex_engineering_biometric_2026-08.csv"),
    ("globex", "globex_support_muster_2026-08.xlsx"),
]


@pytest.mark.parametrize(("tenant", "filename"), NATIVE)
def test_native_files_match_the_ground_truth(
    ingest: Callable[..., dict[str, Any]], database: Database, system_ctx: Ctx, tenant: str, filename: str
) -> None:
    ctx = system_ctx(tenant)
    admin = ctx  # the system context has ingest permission and tenant-wide scope
    outcome = ingest(admin, SAMPLES / tenant / "inputs" / filename)
    job = outcome["job"]
    if outcome["receipt"].status == "duplicate":
        pytest.skip("already ingested in this test database")
    assert job["status"] == "completed", job
    expected = {row["record_key"]: row for row in _ground_truth(tenant) if row["source_file"] == filename}
    stored = {record.record_key: record for record in _records(database, ctx, filename)}
    assert set(stored) == set(expected)
    for key, truth in expected.items():
        record = stored[key]
        assert record.source_page_or_row == truth["source_page_or_row"], key
        assert record.status == truth["status"], key
        assert record.raw_status == truth["raw_status"], key
        assert (record.check_in.strftime("%H:%M") if record.check_in else None) == truth["check_in"], key
        assert record.remarks == truth["remarks"], key
        assert record.remarks_sensitivity == truth["remarks_sensitivity"], key
        assert record.extraction_method == truth["extraction_method"], key
    assert job["counts"]["records_created"] == len(expected)


def test_scanned_register_flags_uncertain_cells_instead_of_guessing(
    ingest: Callable[..., dict[str, Any]], database: Database, system_ctx: Ctx
) -> None:
    if TESSERACT is None:
        pytest.skip("Tesseract is not installed")
    ctx = system_ctx("acme")
    filename = "acme_hr_register_scanned_2026-08.pdf"
    outcome = ingest(ctx, SAMPLES / "acme" / "inputs" / filename)
    if outcome["receipt"].status == "duplicate":
        pytest.skip("already ingested in this test database")
    expected = {row["record_key"]: row for row in _ground_truth("acme") if row["source_file"] == filename}
    stored = {record.record_key: record for record in _records(database, ctx, filename)}
    assert len(stored) >= 0.95 * len(expected)
    for key in ("9fb901162e5e0469", "7a1b8e6e9e6e251e"):  # the two deliberately degraded cells
        if key in stored:
            assert stored[key].review_status == "needs_review", key
    for key, record in stored.items():
        truth = expected.get(key)
        if truth is None or record.review_status == "needs_review":
            continue
        # Anything accepted automatically must be right: uncertain values are never presented as facts.
        assert record.status == truth["status"], key
        assert (record.check_in.strftime("%H:%M") if record.check_in else None) == truth["check_in"], key


def _moved_to(year: int, path: Path, tmp_path: Path) -> Path:
    """The same file with its August 2026 dates moved to August of another year. The content is otherwise
    identical, but its records (one per employee and day) collide with no other test's records: another
    file that already holds an employee's day would, rightly, make these rows fail. Each test that uses
    this picks its own year."""
    target = tmp_path / f"{year}_{path.parent.name}_{path.name}"
    target.write_bytes(path.read_bytes().replace(b"2026-08-", f"{year}-08-".encode()))
    return target


def test_reupload_is_idempotent_and_a_changed_version_updates_only_what_changed(
    ingest: Callable[..., dict[str, Any]], database: Database, system_ctx: Ctx, tmp_path: Path
) -> None:
    ctx = system_ctx("acme")
    inputs, scenarios = SAMPLES / "acme" / "inputs", SAMPLES / "acme" / "scenarios" / "changed_file"
    original = _moved_to(2031, inputs / "acme_engineering_biometric_2026-08.csv", tmp_path)
    changed = _moved_to(2031, scenarios / "acme_engineering_biometric_2026-08.csv", tmp_path)
    name = "idempotency_check.csv"  # one logical file, uploaded three times
    first = ingest(ctx, original, name=name)
    if first["receipt"].status == "duplicate":
        pytest.skip("already ingested in this test database")
    assert first["job"]["status"] == "completed", first["job"]
    created = first["job"]["counts"]["records_created"]
    assert created == 79

    again = ingest(ctx, original, name=name)
    assert again["receipt"].status == "duplicate"
    assert again["receipt"].duplicate_of_job_id == first["receipt"].job_id
    assert len(_records(database, ctx, name)) == created

    second = ingest(ctx, changed, name=name)
    counts = second["job"]["counts"]
    assert second["receipt"].version_no == 2
    assert (counts["records_updated"], counts["records_unchanged"]) == (2, created - 2)
    assert len(_records(database, ctx, name)) == created


def test_injected_instructions_are_quarantined(
    ingest: Callable[..., dict[str, Any]], system_ctx: Ctx, tmp_path: Path
) -> None:
    ctx = system_ctx("acme")
    memo = _unique(
        SAMPLES / "acme" / "scenarios" / "prompt_injection" / "acme_operations_memo_2026-08.docx",
        tmp_path,
        "q",
    )
    outcome = ingest(ctx, memo, entity="OPS")
    if outcome["receipt"].status == "duplicate":
        pytest.skip("already ingested in this test database")
    counts = outcome["job"]["counts"]
    assert counts["records_created"] == 0
    assert counts["chunks_quarantined"] >= 1


ENGINEERING_CSV = SAMPLES / "acme" / "inputs" / "acme_engineering_biometric_2026-08.csv"


class ForgetfulFileStore(FileStore):
    """Loses the first stored file before it is processed, as a host without a persistent disk can."""

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.lost = False

    def path_for(self, tenant_id: str, stored_path: str) -> Path:
        if not self.lost:
            self.lost = True
            raise FileNotFoundError("the stored upload is gone")
        return super().path_for(tenant_id, stored_path)


class DroppedQueue:
    """Accepts jobs and never runs them, like a request stopped before it could process its upload."""

    def enqueue(self, message: JobMessage) -> None:
        return None


def test_a_failed_upload_is_processed_again_when_uploaded_again(
    database: Database, directory: Directory, system_ctx: Ctx, tmp_path: Path
) -> None:
    ctx = system_ctx("acme")
    files = ForgetfulFileStore(tmp_path / "store")
    service = _service(database, directory, files, _inline_queue(database, directory, files))
    data = _moved_to(2032, ENGINEERING_CSV, tmp_path).read_bytes()

    first = service.submit(ctx, filename="retry_check.csv", data=data, entity_id=None)
    assert service.get_job(ctx, first.job_id)["status"] == "failed"

    again = service.submit(ctx, filename="retry_check.csv", data=data, entity_id=None)
    assert again.status == "queued"
    assert again.job_id != first.job_id
    assert again.version_no == first.version_no == 1
    job = service.get_job(ctx, again.job_id)
    assert job["status"] == "completed", job
    assert job["counts"]["records_created"] == 79

    # Once it has been processed, the same file is a duplicate again.
    third = service.submit(ctx, filename="retry_check.csv", data=data, entity_id=None)
    assert third.status == "duplicate"
    assert third.duplicate_of_job_id == first.job_id


def test_a_job_cut_short_is_shown_as_failed_and_can_be_retried(
    database: Database, directory: Directory, system_ctx: Ctx, tmp_path: Path
) -> None:
    ctx = system_ctx("acme")
    files = FileStore(tmp_path / "store")
    data = _moved_to(2033, ENGINEERING_CSV, tmp_path).read_bytes()
    lost = _service(database, directory, files, DroppedQueue()).submit(
        ctx, filename="interrupted_check.csv", data=data, entity_id=None
    )
    inline = _service(database, directory, files, _inline_queue(database, directory, files))
    assert inline.get_job(ctx, lost.job_id)["status"] == "queued"  # recent: it may still be running

    with database.session_for(ctx) as session:
        session.execute(
            update(IngestionJob)
            .where(IngestionJob.id == uuid.UUID(lost.job_id))
            .values(updated_at=datetime.now(UTC) - timedelta(minutes=30))
        )
    # With a worker (queue mode), the worker's sweep owns stalled jobs, so the API leaves them alone.
    worker_mode = _service(database, directory, files, DroppedQueue(), interrupted_after=None)
    assert worker_mode.get_job(ctx, lost.job_id)["status"] == "queued"

    listed = {job["job_id"]: job for job in inline.list_jobs(ctx, limit=100)}
    assert listed[lost.job_id]["status"] == "failed"
    assert inline.get_job(ctx, lost.job_id)["last_error"] == INTERRUPTED_MESSAGE

    retried = inline.submit(ctx, filename="interrupted_check.csv", data=data, entity_id=None)
    assert retried.status == "queued"
    job = inline.get_job(ctx, retried.job_id)
    assert job["status"] == "completed", job
    assert job["counts"]["records_created"] == 79
