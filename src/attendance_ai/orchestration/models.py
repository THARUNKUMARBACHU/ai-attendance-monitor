"""Request and response models for question answering (the API contract)."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Outcome = Literal["answered", "unavailable", "denied", "needs_review", "out_of_scope"]


class QueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=3, max_length=1000, examples=["Who was present on 5 August 2026?"])
    as_of: date | None = Field(
        default=None, description="Reference date for relative dates like 'last month'."
    )


class CitationOut(BaseModel):
    id: str
    kind: Literal["records", "document"]
    source_file: str
    locations: str
    record_count: int | None = None
    snippet: str | None = None


class ConfidenceOut(BaseModel):
    score: float
    band: Literal["high", "medium", "low"]
    reasons: list[str] = []


class ComputationOut(BaseModel):
    sql: str | None = None
    row_count: int = 0
    excluded_pending_review: int = 0
    result_preview: list[dict[str, Any]] = []


class VersionsOut(BaseModel):
    model: str | None
    prompts: str
    data: int
    knowledge: int


class FeedbackUsedOut(BaseModel):
    """An approved reviewer example that guided this answer."""

    example_id: str
    version: int = Field(description="The knowledge version at which the example was activated.")
    similarity: float = Field(description="How close this question is to the example's question (0-1).")


class QueryResponse(BaseModel):
    request_id: str
    outcome: Outcome
    reason_code: str | None = None
    message: str | None = None
    answer: str | None = None
    retrieval_mode: Literal["structured", "document", "hybrid"] | None = None
    confidence: ConfidenceOut | None = None
    citations: list[CitationOut] = []
    computation: ComputationOut | None = None
    feedback_applied: list[FeedbackUsedOut] = []
    context: dict[str, Any]
    versions: VersionsOut
    cached: bool = False
    latency_ms: int = 0
