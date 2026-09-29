"""One client for every OpenAI-compatible API: OpenRouter, OpenAI, Ollama, NVIDIA NIM, vLLM."""

from __future__ import annotations

import time

import openai
from openai import OpenAI

from attendance_ai.generation.llm import LLMError, LLMReply, parse_json_object


class OpenAICompatibleProvider:
    def __init__(self, *, name: str, model: str, base_url: str, api_key: str, timeout_seconds: float) -> None:
        self.name = name
        self.model = model
        # Retries are the router's job (it falls back to the next model instead).
        self._client = OpenAI(base_url=base_url, api_key=api_key, timeout=timeout_seconds, max_retries=0)

    def complete_json(self, *, system: str, user: str, max_tokens: int) -> LLMReply:
        started = time.perf_counter()
        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                temperature=0,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
            )
        except openai.OpenAIError as exc:
            raise LLMError(f"{type(exc).__name__} from {self.name}") from exc
        if not response.choices:
            raise LLMError(f"{self.name} returned no choices.")
        text = response.choices[0].message.content or ""
        usage = response.usage.model_dump() if response.usage else {}
        return LLMReply(
            content=parse_json_object(text),
            provider=self.name,
            model=self.model,
            latency_ms=round((time.perf_counter() - started) * 1000),
            usage=usage,
        )
