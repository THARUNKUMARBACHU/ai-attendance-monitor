"""Inline ingestion (INGESTION_MODE=inline): the upload request processes its own job, for hosts that
cannot run a background worker, such as Vercel.

The job still moves through the same states in PostgreSQL, so the API and the UI see it exactly as they
would with the worker. Temporary failures are retried here, after a short pause, instead of by the
queue's backoff; the pipeline counts the attempts and fails the job once they run out.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from attendance_ai.ingestion.pipeline import RetryLaterError
from attendance_ai.ingestion.queue import MAX_ATTEMPTS, JobHandler, JobMessage

logger = logging.getLogger(__name__)


class InlineJobQueue:
    """A job queue that runs each job as soon as it is queued, in the calling thread."""

    def __init__(
        self,
        handler: JobHandler,
        *,
        pause_seconds: float = 2.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._handler = handler
        self._pause = pause_seconds
        self._sleep = sleep

    def enqueue(self, message: JobMessage) -> None:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                self._handler(message.job_id, message.tenant_id, message.product_id, message.module)
                return
            except RetryLaterError:
                if attempt == MAX_ATTEMPTS:
                    raise
                logger.warning(
                    "inline_job_retry", extra={"fields": {"job_id": message.job_id, "attempt": attempt}}
                )
                self._sleep(self._pause * attempt)
