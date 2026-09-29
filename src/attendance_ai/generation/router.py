"""Provider fallback: try each model in order; skip one whose circuit breaker is open.

The breaker state lives in Redis, so every API process shares it. A Redis problem never blocks a
call: the breaker then simply stays closed.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

import redis
from redis.exceptions import RedisError

from attendance_ai.generation.llm import LLMError, LLMProvider, LLMReply, ProviderUnavailableError

logger = logging.getLogger(__name__)


class CircuitBreaker:
    """Opens after ``threshold`` failures within ``window_seconds``; closes when the window expires."""

    def __init__(self, client: redis.Redis | None, *, threshold: int = 3, window_seconds: int = 60) -> None:
        self._client = client
        self._threshold = threshold
        self._window = window_seconds

    def is_open(self, model: str) -> bool:
        if self._client is None:
            return False
        try:
            value = self._client.get(self._key(model))
        except RedisError:
            return False
        return value is not None and int(value) >= self._threshold

    def record_failure(self, model: str) -> None:
        if self._client is None:
            return
        try:
            key = self._key(model)
            count = self._client.incr(key)
            if count == 1:
                self._client.expire(key, self._window)
        except RedisError:
            logger.warning("circuit_breaker_unavailable", exc_info=True)

    def record_success(self, model: str) -> None:
        if self._client is None:
            return
        try:
            self._client.delete(self._key(model))
        except RedisError:
            logger.warning("circuit_breaker_unavailable", exc_info=True)

    @staticmethod
    def _key(model: str) -> str:
        return f"attn:breaker:{model}"


@dataclass(frozen=True, slots=True)
class RoutedReply:
    reply: LLMReply
    attempts: list[str]

    @property
    def used_fallback(self) -> bool:
        return len(self.attempts) > 1


class LLMRouter:
    def __init__(self, providers: Sequence[LLMProvider], breaker: CircuitBreaker | None = None) -> None:
        self._providers = list(providers)
        self._breaker = breaker or CircuitBreaker(None)

    @property
    def primary_model(self) -> str | None:
        return self._providers[0].model if self._providers else None

    def complete_json(self, *, system: str, user: str, max_tokens: int) -> RoutedReply:
        attempts: list[str] = []
        for provider in self._providers:
            if self._breaker.is_open(provider.model):
                attempts.append(f"{provider.model}: skipped (circuit open)")
                continue
            try:
                reply = provider.complete_json(system=system, user=user, max_tokens=max_tokens)
            except LLMError as exc:
                self._breaker.record_failure(provider.model)
                attempts.append(f"{provider.model}: failed ({exc})")
                logger.warning(
                    "llm_call_failed", extra={"fields": {"model": provider.model, "error": str(exc)}}
                )
                continue
            self._breaker.record_success(provider.model)
            attempts.append(f"{provider.model}: ok")
            return RoutedReply(reply=reply, attempts=attempts)
        raise ProviderUnavailableError(attempts)
