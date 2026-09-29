"""Redis connection used for the answer cache and LLM provider health (the job queue has its own
connection through the Dramatiq broker in stores/queue.py)."""

from __future__ import annotations

import redis

from attendance_ai.core.config import Settings


def create_redis(settings: Settings) -> redis.Redis:
    timeout = settings.health_check_timeout_seconds
    return redis.Redis.from_url(
        settings.redis_url.get_secret_value(),
        socket_connect_timeout=timeout,
        socket_timeout=timeout,
        health_check_interval=30,
        decode_responses=True,
    )
