"""Background job queue: a Dramatiq broker on Redis.

Dramatiq (rather than RQ) because its worker also runs natively on Windows. PostgreSQL stays the
source of truth for job status; Redis only carries job IDs to the worker.
"""

from __future__ import annotations

import dramatiq
from dramatiq.brokers.redis import RedisBroker

from attendance_ai.core.config import Settings


def create_broker(settings: Settings) -> RedisBroker:
    broker = RedisBroker(url=settings.redis_url.get_secret_value())  # type: ignore[no-untyped-call]
    dramatiq.set_broker(broker)
    return broker
