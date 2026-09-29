"""Answer cache in Redis. The key covers everything that can change an answer: the caller's full
access scope, the question, and the data, knowledge, prompt and model versions. Two users with
different access never share an entry, and new data or feedback invalidates old entries naturally."""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

import redis
from redis.exceptions import RedisError

from attendance_ai.core.access import AccessContext

logger = logging.getLogger(__name__)

KEY_PREFIX = "attn:answer:"


def cache_key(ctx: AccessContext, question: str, *, as_of: str, versions: dict[str, Any]) -> str:
    material = {
        "tenant": ctx.tenant_id,
        "product": ctx.product_id,
        "module": ctx.module,
        "scope": ctx.scope.value,
        "entities": sorted(ctx.entity_scope),
        "employee": ctx.employee_id if ctx.scope.value == "self" else None,
        "clearance": ctx.clearance,
        "question": " ".join(question.lower().split()),
        "as_of": as_of,
        "versions": versions,
    }
    digest = hashlib.sha256(json.dumps(material, sort_keys=True).encode("utf-8")).hexdigest()
    return KEY_PREFIX + digest


class AnswerCache:
    """Best effort: a Redis problem means a cache miss, never a failed request."""

    def __init__(self, client: redis.Redis, ttl_seconds: int) -> None:
        self._client = client
        self._ttl = ttl_seconds

    def get(self, key: str) -> dict[str, Any] | None:
        if self._ttl <= 0:
            return None
        try:
            raw = self._client.get(key)
        except RedisError:
            logger.warning("answer_cache_unavailable", exc_info=True)
            return None
        return json.loads(raw) if isinstance(raw, str | bytes) else None

    def put(self, key: str, value: dict[str, Any]) -> None:
        if self._ttl <= 0:
            return
        try:
            self._client.set(key, json.dumps(value, default=str), ex=self._ttl)
        except RedisError:
            logger.warning("answer_cache_unavailable", exc_info=True)
