"""Ingestion worker process: consumes jobs from the Redis queue and runs the ingestion pipeline.

Run with:  uv run python -m attendance_ai.worker
"""

import logging
import signal
import threading
from datetime import timedelta

import dramatiq

from attendance_ai.core.config import get_settings
from attendance_ai.core.directory import Directory
from attendance_ai.core.logging import configure_logging
from attendance_ai.governance.audit import AuditLog
from attendance_ai.ingestion.indexing import DocumentIndexer
from attendance_ai.ingestion.pipeline import IngestionPipeline
from attendance_ai.ingestion.queue import DramatiqJobQueue, JobMessage, install_handler
from attendance_ai.stores.db import Database
from attendance_ai.stores.embeddings import Embedder
from attendance_ai.stores.file_store import FileStore
from attendance_ai.stores.queue import create_broker
from attendance_ai.stores.vector_store import VectorStore

logger = logging.getLogger(__name__)

SWEEP_EVERY_SECONDS = 300
STALE_AFTER = timedelta(minutes=10)


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    directory = Directory.from_file(settings.seed_file)
    database = Database(settings)
    indexer = None
    if settings.qdrant_url:
        embedder = Embedder.from_settings(settings)
        indexer = DocumentIndexer(VectorStore.from_settings(settings, embedder.dense_size), embedder)
    pipeline = IngestionPipeline(
        settings=settings,
        directory=directory,
        database=database,
        file_store=FileStore(settings.storage_dir),
        indexer=indexer,
        audit=AuditLog(database),
    )
    broker = create_broker(settings)
    queue = DramatiqJobQueue(broker)
    install_handler(pipeline.run)

    def sweep() -> None:
        """Re-queue jobs left queued (for example, enqueueing failed after commit) or stuck running."""
        for tenant_id in directory.tenant_ids:
            for product_id, module in directory.product_modules:
                ctx = directory.system_context(
                    tenant_id=tenant_id, product_id=product_id, module=module, request_id="sweep"
                )
                try:
                    for job_id in pipeline.requeue_stale(ctx, older_than=STALE_AFTER):
                        queue.enqueue(JobMessage(job_id, tenant_id, product_id, module))
                        logger.warning("job_requeued", extra={"fields": {"job_id": job_id}})
                except Exception:
                    logger.exception("sweep_failed", extra={"fields": {"tenant_id": tenant_id}})

    worker = dramatiq.Worker(broker, worker_threads=settings.worker_threads)
    stop = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stop.set())
    worker.start()
    logger.info("worker_started", extra={"fields": {"threads": settings.worker_threads}})
    sweep()
    try:
        # Wake up every second so Ctrl+C is handled promptly on Windows too.
        waited = 0
        while not stop.wait(timeout=1.0):
            waited += 1
            if waited >= SWEEP_EVERY_SECONDS:
                waited = 0
                sweep()
    finally:
        worker.stop()
        database.dispose()
        logger.info("worker_stopped")


if __name__ == "__main__":
    main()
