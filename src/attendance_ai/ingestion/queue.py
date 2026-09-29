"""The ingestion job queue: Dramatiq on Redis. PostgreSQL holds job status; messages carry only the
job ID and its isolation context. The worker installs the pipeline that processes each message."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

import dramatiq

MAX_ATTEMPTS = 3
ACTOR_NAME = "process_ingestion_job"
QUEUE_NAME = "ingestion"


@dataclass(frozen=True, slots=True)
class JobMessage:
    job_id: str
    tenant_id: str
    product_id: str
    module: str


class JobQueue(Protocol):
    def enqueue(self, message: JobMessage) -> None: ...


JobHandler = Callable[[str, str, str, str], None]
_handler: JobHandler | None = None


def install_handler(handler: JobHandler) -> None:
    """Called by the worker process at start-up."""
    global _handler
    _handler = handler


def _process(job_id: str, tenant_id: str, product_id: str, module: str) -> None:
    if _handler is None:
        raise RuntimeError("No ingestion handler installed; is this the worker process?")
    _handler(job_id, tenant_id, product_id, module)


class DramatiqJobQueue:
    def __init__(self, broker: Any) -> None:
        self._actor = dramatiq.actor(
            broker=broker,
            actor_name=ACTOR_NAME,
            queue_name=QUEUE_NAME,
            max_retries=MAX_ATTEMPTS - 1,
            min_backoff=5_000,
            max_backoff=120_000,
            time_limit=30 * 60 * 1000,
        )(_process)

    def enqueue(self, message: JobMessage) -> None:
        self._actor.send(message.job_id, message.tenant_id, message.product_id, message.module)
