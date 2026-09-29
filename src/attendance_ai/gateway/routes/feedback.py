"""The feedback/training API. Only reviewers (the `feedback:submit` permission, tenant admins in the seed)
may submit, list or roll back examples, and only within their own tenant."""

from typing import Annotated, Literal, cast

from fastapi import APIRouter, Depends, Query, Request

from attendance_ai.core.access import AccessContext
from attendance_ai.feedback.models import FeedbackExampleOut, FeedbackListOut, FeedbackRequest
from attendance_ai.feedback.service import FeedbackService
from attendance_ai.gateway.deps import require_permission

router = APIRouter(prefix="/api/v1/feedback", tags=["feedback"])

Reviewer = Annotated[AccessContext, Depends(require_permission("feedback:submit"))]


def _service(request: Request) -> FeedbackService:
    return cast(FeedbackService, request.app.state.feedback)


@router.post("", status_code=201, response_model=FeedbackExampleOut)
def submit_feedback(request: Request, ctx: Reviewer, body: FeedbackRequest) -> FeedbackExampleOut:
    """Correct an answer. The example is validated and replayed; it is `active` (with its version and
    the replayed answer as evidence) or `rejected` (with the reasons)."""
    return _service(request).submit(
        ctx,
        request_id=body.request_id,
        feedback=body.feedback,
        ideal_output=body.ideal_final_output,
        approval_note=body.approval_note,
    )


@router.get("", response_model=FeedbackListOut)
def list_feedback(
    request: Request,
    ctx: Reviewer,
    status: Literal["active", "inactive", "rejected"] | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> FeedbackListOut:
    return FeedbackListOut(items=_service(request).list_examples(ctx, status=status, limit=limit))


@router.get("/{example_id}", response_model=FeedbackExampleOut)
def get_feedback(request: Request, ctx: Reviewer, example_id: str) -> FeedbackExampleOut:
    return _service(request).get(ctx, example_id)


@router.post("/{example_id}/deactivate", response_model=FeedbackExampleOut)
def deactivate_feedback(request: Request, ctx: Reviewer, example_id: str) -> FeedbackExampleOut:
    """Roll an example back. It stops applying from the returned knowledge version, and cached answers
    made with it are no longer served."""
    return _service(request).deactivate(ctx, example_id)
