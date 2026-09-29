"""The provider interface: one JSON-in, JSON-out call. Everything about access, retrieval and
validation happens outside it; a provider only ever sees the prompt it is given."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Protocol


class LLMError(RuntimeError):
    """A provider call failed or returned something unusable."""


class ProviderUnavailableError(LLMError):
    """Every provider in the chain failed or was skipped."""

    def __init__(self, attempts: list[str]) -> None:
        super().__init__("No language model is available right now.")
        self.attempts = attempts


@dataclass(frozen=True, slots=True)
class LLMReply:
    content: dict[str, Any]
    provider: str
    model: str
    latency_ms: int
    usage: dict[str, Any] = field(default_factory=dict)


class LLMProvider(Protocol):
    name: str
    model: str

    def complete_json(self, *, system: str, user: str, max_tokens: int) -> LLMReply: ...


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def parse_json_object(text: str) -> dict[str, Any]:
    """Parse a model's JSON reply, tolerating a Markdown code fence around it."""
    cleaned = _FENCE.sub("", text.strip())
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise LLMError("The model did not return valid JSON.") from exc
    if not isinstance(value, dict):
        raise LLMError("The model returned JSON that is not an object.")
    return value
