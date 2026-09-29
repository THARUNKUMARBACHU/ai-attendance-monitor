"""Export records, or one question's answer and evidence, as JSON, Excel or PDF. Access is re-checked
for the caller at export time, and every format contains the same records and source references."""

from datetime import date
from typing import Annotated, Literal, cast

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator

from attendance_ai.core.access import AccessContext
from attendance_ai.exports.render import MEDIA_TYPES
from attendance_ai.exports.service import ExportService
from attendance_ai.gateway.deps import require_permission
from attendance_ai.gateway.routes.records import Review, Status
from attendance_ai.stores.records import RecordFilter

router = APIRouter(prefix="/api/v1", tags=["exports"])

Exporter = Annotated[AccessContext, Depends(require_permission("export"))]


class RecordsFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date_from: date | None = None
    date_to: date | None = None
    entity_id: str | None = Field(default=None, max_length=32)
    employee_id: str | None = Field(default=None, max_length=32)
    status: Status | None = None
    review_status: Review | None = None
    source_file: str | None = Field(default=None, max_length=200)


class ExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: Literal["json", "xlsx", "pdf"]
    request_id: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9._-]{8,64}$",
        description="Export this question's answer, citations and evidence records.",
    )
    records: RecordsFilter | None = Field(
        default=None, description="Export the records matching these filters (default: all you can see)."
    )

    @model_validator(mode="after")
    def _one_dataset(self) -> "ExportRequest":
        if self.request_id and self.records is not None:
            raise ValueError("Give either request_id or records, not both.")
        return self


@router.post(
    "/exports",
    response_class=Response,
    responses={
        200: {
            "description": "The export file, as an attachment.",
            "content": {media_type: {} for media_type in MEDIA_TYPES.values()},
        }
    },
)
def create_export(request: Request, ctx: Exporter, body: ExportRequest) -> Response:
    service = cast(ExportService, request.app.state.exports)
    filters = RecordFilter(**body.records.model_dump()) if body.records else None
    rendered = service.export(ctx, fmt=body.format, request_id=body.request_id, filters=filters)
    return Response(
        content=rendered.content,
        media_type=rendered.media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{rendered.filename}"',
            "Cache-Control": "no-store",
        },
    )
