"""A scripted provider for tests: deterministic, offline and free."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from attendance_ai.generation.llm import LLMError, LLMReply

Responder = Callable[[str, str], dict[str, Any]]


class MockProvider:
    """Answers with ``responder(system, user)``. Raises LLMError while ``failing`` is set."""

    def __init__(self, responder: Responder, *, model: str = "mock-llm") -> None:
        self.name = "mock"
        self.model = model
        self.failing = False
        self.calls: list[tuple[str, str]] = []
        self._responder = responder

    def complete_json(self, *, system: str, user: str, max_tokens: int) -> LLMReply:
        self.calls.append((system, user))
        if self.failing:
            raise LLMError("mock provider is down")
        return LLMReply(
            content=self._responder(system, user), provider=self.name, model=self.model, latency_ms=0
        )
