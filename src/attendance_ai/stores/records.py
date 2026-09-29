"""Browse normalised records within the caller's access (row-level security applies), including those
waiting for review, which questions never use. Restricted remarks are masked below restricted clearance."""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import func, select

from attendance_ai.core.access import AccessContext, clearance_rank
from attendance_ai.stores.db import Database
from attendance_ai.stores.models import AttendanceRecord


@dataclass(frozen=True, slots=True)
class RecordFilter:
    date_from: date | None = None
    date_to: date | None = None
    entity_id: str | None = None
    employee_id: str | None = None
    status: str | None = None
    review_status: str | None = None
    source_file: str | None = None


def browse_records(
    database: Database, ctx: AccessContext, filters: RecordFilter, *, limit: int, offset: int
) -> dict[str, Any]:
    query = select(AttendanceRecord).where(AttendanceRecord.is_active)
    if filters.date_from:
        query = query.where(AttendanceRecord.attendance_date >= filters.date_from)
    if filters.date_to:
        query = query.where(AttendanceRecord.attendance_date <= filters.date_to)
    if filters.entity_id:
        query = query.where(AttendanceRecord.entity_id == filters.entity_id)
    if filters.employee_id:
        query = query.where(AttendanceRecord.employee_id == filters.employee_id.strip().upper())
    if filters.status:
        query = query.where(AttendanceRecord.status == filters.status)
    if filters.review_status:
        query = query.where(AttendanceRecord.review_status == filters.review_status)
    if filters.source_file:
        query = query.where(AttendanceRecord.source_file == filters.source_file)
    with database.session_for(ctx) as session:
        total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
        rows = session.scalars(
            query.order_by(
                AttendanceRecord.attendance_date, AttendanceRecord.employee_id, AttendanceRecord.id
            )
            .limit(limit)
            .offset(offset)
        ).all()
        items = [record_view(row, ctx) for row in rows]
    return {"items": items, "total": int(total), "limit": limit, "offset": offset}


def records_by_ids(database: Database, ctx: AccessContext, ids: Iterable[str]) -> list[dict[str, Any]]:
    """The active records among these IDs that the caller may see now. IDs that are not record IDs
    (for example vector-store point IDs), or that the caller may not see, are skipped."""
    keys: list[uuid.UUID] = []
    for value in ids:
        try:
            keys.append(uuid.UUID(str(value)))
        except ValueError:
            continue
    if not keys:
        return []
    with database.session_for(ctx) as session:
        rows = session.scalars(
            select(AttendanceRecord)
            .where(AttendanceRecord.id.in_(keys), AttendanceRecord.is_active)
            .order_by(AttendanceRecord.attendance_date, AttendanceRecord.employee_id, AttendanceRecord.id)
        ).all()
        return [record_view(row, ctx) for row in rows]


def record_view(row: AttendanceRecord, ctx: AccessContext) -> dict[str, Any]:
    """One record as callers see it: restricted remarks are masked below restricted clearance."""
    hidden = row.remarks_sensitivity == "restricted" and ctx.clearance < clearance_rank("restricted")
    return {
        "record_id": str(row.id),
        "record_key": row.record_key,
        "attendance_date": row.attendance_date.isoformat(),
        "employee_id": row.employee_id,
        "employee_name": row.employee_name,
        "department_id": row.entity_id,
        "department_name": row.department,
        "status": row.status,
        "check_in": row.check_in.strftime("%H:%M") if row.check_in else None,
        "check_out": row.check_out.strftime("%H:%M") if row.check_out else None,
        "total_hours": float(row.total_hours) if row.total_hours is not None else None,
        "remarks": "[restricted]" if hidden and row.remarks else row.remarks,
        "review_status": row.review_status,
        "extraction_method": row.extraction_method,
        "extraction_confidence": float(row.extraction_confidence),
        "source_file": row.source_file,
        "source_page_or_row": row.source_page_or_row,
    }
