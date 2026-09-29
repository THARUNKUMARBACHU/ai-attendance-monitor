"""The data behind an export. It is always built under the caller's current access (row-level security
and remark masking apply), never inherited from whoever asked the original question."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, Literal

from attendance_ai.core.access import AccessContext, Scope, clearance_rank
from attendance_ai.core.directory import Directory
from attendance_ai.core.errors import NotFoundError, PermissionDeniedError
from attendance_ai.orchestration.answer import scope_description
from attendance_ai.stores.db import Database
from attendance_ai.stores.models import QueryLogEntry
from attendance_ai.stores.records import RecordFilter, browse_records, records_by_ids
from attendance_ai.stores.versions import current_versions

# Every format carries exactly these record fields, in this order.
RECORD_FIELDS: tuple[tuple[str, str], ...] = (
    ("attendance_date", "Date"),
    ("employee_id", "Employee ID"),
    ("employee_name", "Employee name"),
    ("department_id", "Department ID"),
    ("department_name", "Department"),
    ("status", "Status"),
    ("check_in", "Check-in"),
    ("check_out", "Check-out"),
    ("total_hours", "Total hours"),
    ("remarks", "Remarks"),
    ("review_status", "Review status"),
    ("extraction_method", "Extraction method"),
    ("extraction_confidence", "Extraction confidence"),
    ("source_file", "Source file"),
    ("source_page_or_row", "Source location"),
    ("record_key", "Record key"),
    ("record_id", "Record ID"),
)

REVIEW_NOTE = (
    "Records waiting for review are included and marked in the review status; "
    "answers to questions never use them."
)


@dataclass(frozen=True, slots=True)
class ExportDataset:
    kind: Literal["records", "query"]
    title: str
    meta: dict[str, Any]
    records: list[dict[str, Any]]
    query: dict[str, Any] | None = None
    result: list[dict[str, Any]] = field(default_factory=list)


def records_dataset(
    database: Database, directory: Directory, ctx: AccessContext, filters: RecordFilter, *, cap: int
) -> ExportDataset:
    page = browse_records(database, ctx, filters, limit=cap, offset=0)
    records = [_ordered(item) for item in page["items"]]
    meta = _meta(database, directory, ctx, title="Attendance records", records=records)
    meta["filters"] = {
        name: value.isoformat() if isinstance(value, date) else value
        for name, value in dataclasses.asdict(filters).items()
        if value is not None
    }
    meta["total_matching"] = page["total"]
    meta["truncated"] = page["total"] > len(records)
    meta["note"] = REVIEW_NOTE
    return ExportDataset("records", "Attendance records", meta, records)


def query_dataset(
    database: Database, directory: Directory, ctx: AccessContext, request_id: str, *, cap: int
) -> ExportDataset:
    """A question, its answer and citations, and the records behind it that the caller may see now."""
    with database.session_for(ctx) as session:
        entry = session.get(QueryLogEntry, request_id)  # row-level security: own questions, or admin
        if entry is None:
            raise NotFoundError("No such question in your history.")
        if entry.user_id == ctx.user_id and entry.role != ctx.role:
            raise PermissionDeniedError(
                "Your access has changed since this question was asked. Ask it again to export it."
            )
        response = dict(entry.answer or {})
        query = {
            "request_id": entry.request_id,
            "question": entry.question_masked,
            "asked_at": entry.created_at.isoformat(timespec="seconds"),
            "asked_by": entry.user_id,
            "outcome": response.get("outcome"),
            "reason_code": response.get("reason_code"),
            "message": response.get("message"),
            "answer": response.get("answer"),
            "retrieval_mode": response.get("retrieval_mode"),
            "confidence": response.get("confidence"),
            "citations": list(response.get("citations") or []),
            "feedback_applied": list(response.get("feedback_applied") or []),
            "versions": response.get("versions"),
        }
        evidence_ids = [str(value) for value in entry.retrieved_ids or []]
    visible = records_by_ids(database, ctx, evidence_ids)
    records = [_ordered(item) for item in visible[:cap]]
    result = list((response.get("computation") or {}).get("result_preview") or [])
    meta = _meta(database, directory, ctx, title="Question and answer", records=records)
    meta["request_id"] = request_id
    meta["truncated"] = len(visible) > len(records)
    meta["note"] = (
        "Evidence records are re-read under your current access, so records you can no longer see "
        "are left out."
    )
    return ExportDataset("query", "Question and answer", meta, records, query=query, result=result)


def _ordered(item: dict[str, Any]) -> dict[str, Any]:
    return {name: item.get(name) for name, _ in RECORD_FIELDS}


def _meta(
    database: Database,
    directory: Directory,
    ctx: AccessContext,
    *,
    title: str,
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    tenant = directory.get_tenant(ctx.tenant_id)
    with database.session_for(ctx) as session:
        versions = current_versions(session, ctx)
    # A caller with restricted clearance sees restricted remarks unmasked, so the file is restricted too.
    restricted = ctx.clearance >= clearance_rank("restricted")
    return {
        "title": title,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "generated_by": ctx.user_id,
        "role": ctx.role,
        "tenant_id": ctx.tenant_id,
        "tenant_name": tenant.name if tenant else ctx.tenant_id,
        "product_id": ctx.product_id,
        "module": ctx.module,
        "scope": scope_description(ctx, tenant) if tenant else ctx.scope.value,
        "entity_scope": list(ctx.entity_scope) if ctx.scope is Scope.DEPARTMENT else [],
        "classification": "restricted" if restricted else "confidential",
        "data_version": versions.data,
        "knowledge_version": versions.knowledge,
        "record_count": len(records),
    }
