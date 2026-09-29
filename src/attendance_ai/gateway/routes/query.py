"""Ask a question about attendance. Every outcome (answered, unavailable, denied, needs_review,
out_of_scope) is a normal response; only request-level problems are HTTP errors."""

from typing import Annotated, cast

from fastapi import APIRouter, Depends, Request

from attendance_ai.core.access import AccessContext
from attendance_ai.gateway.deps import require_permission
from attendance_ai.orchestration.answer import AnswerService
from attendance_ai.orchestration.models import QueryRequest, QueryResponse

router = APIRouter(prefix="/api/v1", tags=["query"])

Asker = Annotated[AccessContext, Depends(require_permission("query"))]


@router.post("/query", response_model=QueryResponse)
def ask(request: Request, ctx: Asker, body: QueryRequest) -> QueryResponse:
    service = cast(AnswerService, request.app.state.answers)
    return service.answer(ctx, body)
