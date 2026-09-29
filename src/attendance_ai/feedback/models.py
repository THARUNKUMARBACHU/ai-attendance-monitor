"""Request and response models for the feedback/training API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from attendance_ai.orchestration.models import ConfidenceOut, FeedbackUsedOut


class FeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(
        pattern=r"^[A-Za-z0-9._-]{8,64}$", description="The request_id of the answer being corrected."
    )
    feedback: str = Field(
        min_length=3, max_length=2000, description="What was wrong, missing or undesirable."
    )
    ideal_final_output: str = Field(
        min_length=3, max_length=4000, description="The answer the agent should give instead."
    )
    approval_note: str | None = Field(
        default=None, max_length=500, description="Why the reviewer approves it."
    )


class FeedbackScopeOut(BaseModel):
    scope: Literal["tenant", "department", "self"]
    entity_scope: list[str]
    employee_id: str | None
    clearance: str


class ReplayOut(BaseModel):
    """The original question answered again with the example applied: the evidence for activation."""

    request_id: str
    outcome: str
    reason_code: str | None = None
    answer: str | None = None
    similarity: float = Field(description="Similarity of the replayed answer to the ideal output (0-1).")
    numbers_missing: list[str] = []
    feedback_applied: list[FeedbackUsedOut] = []
    confidence: ConfidenceOut | None = None


class FeedbackExampleOut(BaseModel):
    example_id: str
    status: Literal["active", "inactive", "rejected"]
    version: int = Field(description="The knowledge version at which the example was recorded or activated.")
    scope: FeedbackScopeOut
    request_id: str
    question: str
    feedback: str
    ideal_output: str
    reviewer_id: str
    approval_note: str | None = None
    problems: list[str] = Field(default=[], description="Why a rejected example was rejected.")
    replay: ReplayOut | None = None
    created_at: str
    deactivated_at: str | None = None
    deactivated_by: str | None = None
    knowledge_version: int | None = Field(
        default=None, description="The scope's knowledge version after this change."
    )
    message: str | None = None


class FeedbackListOut(BaseModel):
    items: list[FeedbackExampleOut]
