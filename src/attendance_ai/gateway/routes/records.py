"""Browse normalised attendance records within the caller's access."""

from datetime import date
from typing import Annotated, Any, Literal, cast

from fastapi import APIRouter, Depends, Query, Request

from attendance_ai.core.access import AccessContext
from attendance_ai.gateway.deps import require_permission
from attendance_ai.stores.db import Database
from attendance_ai.stores.records import RecordFilter, browse_records

router = APIRouter(prefix="/api/v1/records", tags=["records"])

Reader = Annotated[AccessContext, Depends(require_permission("query"))]
Status = Literal["PRESENT", "ABSENT", "LEAVE", "WFH", "HALF_DAY", "HOLIDAY", "WEEKLY_OFF"]
Review = Literal["auto_accepted", "needs_review", "verified", "rejected"]


@router.get("")
def list_records(
    request: Request,
    ctx: Reader,
    date_from: date | None = None,
    date_to: date | None = None,
    entity_id: Annotated[str | None, Query(max_length=32)] = None,
    employee_id: Annotated[str | None, Query(max_length=32)] = None,
    status: Status | None = None,
    review_status: Review | None = None,
    source_file: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    filters = RecordFilter(
        date_from=date_from,
        date_to=date_to,
        entity_id=entity_id,
        employee_id=employee_id,
        status=status,
        review_status=review_status,
        source_file=source_file,
    )
    database = cast(Database, request.app.state.database)
    return browse_records(database, ctx, filters, limit=limit, offset=offset)
