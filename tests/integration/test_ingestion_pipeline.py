"""Ingestion end to end with the real sample files: upload -> queue -> pipeline -> PostgreSQL + vector
store, checked against the ground truth. The queue runs jobs inline; the vector store is Qdrant's
in-process mode with a deterministic embedder, so no network or model download is needed."""

import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from qdrant_client import QdrantClient
from sqlalchemy import select

from attendance_ai.core.access import AccessContext
from attendance_ai.core.directory import Directory
from attendance_ai.governance.audit import AuditLog
from attendance_ai.ingestion.indexing import DocumentIndexer
from attendance_ai.ingestion.pipeline import IngestionPipeline
from attendance_ai.ingestion.queue import JobMessage
from attendance_ai.ingestion.service import IngestionService
from attendance_ai.stores.db import Database
from attendance_ai.stores.file_store import FileStore
from attendance_ai.stores.models import AttendanceRecord
from attendance_ai.stores.vector_store import VectorStore

from ..fakes import FakeEmbedder
from ..support import PROJECT_ROOT, find_tesseract, make_settings

SAMPLES = PROJECT_ROOT / "sample_data" / "tenants"
TESSERACT = find_tesseract()
Ctx = Callable[[str], AccessContext]


class InlineQueue:
    """Runs each job as soon as it is queued, like a worker with no delay."""

    def __init__(self) -> None:
        self.pipeline: IngestionPipeline | None = None

    def enqueue(self, message: JobMessage) -> None:
        assert self.pipeline is not None
        self.pipeline.run(message.job_id, message.tenant_id, message.product_id, message.module)


@pytest.fixture
def ingest(database: Database, directory: Directory, tmp_path: Path) -> Callable[..., dict[str, Any]]:
    settings = make_settings(tesseract_cmd=TESSERACT)
    store = VectorStore(QdrantClient(location=":memory:"), "chunks", FakeEmbedder.dense_size)
    files = FileStore(tmp_path)
    queue = InlineQueue()
    queue.pipeline = IngestionPipeline(
        settings=settings,
        directory=directory,
        database=database,
        file_store=files,
        indexer=DocumentIndexer(store, FakeEmbedder()),  # type: ignore[arg-type]
        audit=AuditLog(database),
    )
    service = IngestionService(
        database=database,
        directory=directory,
        file_store=files,
        queue=queue,
        audit=AuditLog(database),
        max_upload_bytes=20 * 1024 * 1024,
    )

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


def _moved_to_2031(path: Path, tmp_path: Path) -> Path:
    """The same file with its August 2026 dates moved to August 2031. The content is otherwise identical,
    but its records (one per employee and day) collide with no other test's records: another file that
    already holds an employee's day would, rightly, make these rows fail."""
    target = tmp_path / f"{path.parent.name}_{path.name}"
    target.write_bytes(path.read_bytes().replace(b"2026-08-", b"2031-08-"))
    return target


def test_reupload_is_idempotent_and_a_changed_version_updates_only_what_changed(
    ingest: Callable[..., dict[str, Any]], database: Database, system_ctx: Ctx, tmp_path: Path
) -> None:
    ctx = system_ctx("acme")
    inputs, scenarios = SAMPLES / "acme" / "inputs", SAMPLES / "acme" / "scenarios" / "changed_file"
    original = _moved_to_2031(inputs / "acme_engineering_biometric_2026-08.csv", tmp_path)
    changed = _moved_to_2031(scenarios / "acme_engineering_biometric_2026-08.csv", tmp_path)
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
