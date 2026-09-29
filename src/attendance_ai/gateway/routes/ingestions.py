"""Upload attendance evidence and follow its processing (admins only)."""

from dataclasses import asdict
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import JSONResponse

from attendance_ai.core.access import AccessContext
from attendance_ai.gateway.deps import get_settings, require_permission
from attendance_ai.ingestion.file_checks import FileTooLargeError
from attendance_ai.ingestion.service import IngestionService

router = APIRouter(prefix="/api/v1/ingestions", tags=["ingestion"])

Ingestor = Annotated[AccessContext, Depends(require_permission("ingest"))]


def _service(request: Request) -> IngestionService:
    return cast(IngestionService, request.app.state.ingestion)


@router.post("", status_code=202)
def upload(
    request: Request,
    ctx: Ingestor,
    file: Annotated[UploadFile, File(description="CSV, XLSX, DOCX or PDF (including scans).")],
    entity_id: Annotated[str | None, Form(description="Department the document belongs to, if any.")] = None,
) -> JSONResponse:
    limit = get_settings(request).upload_max_mb * 1024 * 1024
    data = file.file.read(limit + 1)
    if len(data) > limit:
        raise FileTooLargeError(f"The file is larger than the {limit // (1024 * 1024)} MB limit.")
    receipt = _service(request).submit(
        ctx, filename=file.filename or "upload", data=data, entity_id=(entity_id or None)
    )
    return JSONResponse(asdict(receipt), status_code=202)


@router.get("")
def list_jobs(
    request: Request, ctx: Ingestor, limit: Annotated[int, Query(ge=1, le=100)] = 20
) -> dict[str, Any]:
    return {"items": _service(request).list_jobs(ctx, limit=limit)}


@router.get("/{job_id}")
def get_job(request: Request, ctx: Ingestor, job_id: str) -> dict[str, Any]:
    return _service(request).get_job(ctx, job_id)
