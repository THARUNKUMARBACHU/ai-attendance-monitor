"""Build and render an export for one caller, and audit it."""

from __future__ import annotations

from attendance_ai.core.access import AccessContext
from attendance_ai.core.directory import Directory
from attendance_ai.exports.datasets import query_dataset, records_dataset
from attendance_ai.exports.render import Format, RenderedExport, render
from attendance_ai.governance.audit import AuditEvent, AuditSink
from attendance_ai.stores.db import Database
from attendance_ai.stores.records import RecordFilter


class ExportService:
    def __init__(self, *, database: Database, directory: Directory, audit: AuditSink, row_cap: int) -> None:
        self._db = database
        self._directory = directory
        self._audit = audit
        self._row_cap = row_cap

    def export(
        self,
        ctx: AccessContext,
        *,
        fmt: Format,
        request_id: str | None = None,
        filters: RecordFilter | None = None,
    ) -> RenderedExport:
        """Records matching ``filters`` (default: everything the caller may see), or the answer and
        evidence of the question ``request_id``."""
        ctx.require("export")
        if request_id:
            dataset = query_dataset(self._db, self._directory, ctx, request_id, cap=self._row_cap)
        else:
            dataset = records_dataset(
                self._db, self._directory, ctx, filters or RecordFilter(), cap=self._row_cap
            )
        rendered = render(dataset, fmt)
        self._audit.record(
            AuditEvent(
                "export",
                f"{dataset.kind}_exported",
                "success",
                retrieved_ids=[str(record["record_id"]) for record in dataset.records][:200],
                details={
                    "format": fmt,
                    "kind": dataset.kind,
                    "request_id": request_id,
                    "filters": dataset.meta.get("filters"),
                    "record_count": len(dataset.records),
                    "truncated": bool(dataset.meta.get("truncated")),
                    "bytes": len(rendered.content),
                },
            ),
            ctx,
        )
        return rendered
