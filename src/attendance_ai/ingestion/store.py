"""Write normalised records for one source version.

Idempotent by logical key (tenant, product, module, employee, date): an unchanged record is kept as
it is, a changed one is superseded by a new row, and one missing from the new version is deactivated.
A record that already exists, active, from a different source is rejected rather than overwritten.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from attendance_ai.ingestion.normalize import RowFailure
from attendance_ai.stores.models import AttendanceRecord

COMPARED_FIELDS = (
    "status",
    "raw_status",
    "check_in",
    "check_out",
    "total_hours",
    "remarks",
    "remarks_sensitivity",
    "employee_name",
    "entity_id",
    "department",
    "review_status",
    "classification",
    "source_page_or_row",
)


@dataclass(slots=True)
class StoreOutcome:
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    removed: int = 0
    failures: list[RowFailure] = field(default_factory=list)


def store_records(
    session: Session,
    *,
    source_id: uuid.UUID,
    version_id: uuid.UUID,
    source_file: str,
    records: list[dict[str, Any]],
) -> StoreOutcome:
    outcome = StoreOutcome()
    existing = {
        row.record_key: row
        for row in session.scalars(
            select(AttendanceRecord).where(
                AttendanceRecord.source_id == source_id, AttendanceRecord.is_active
            )
        )
    }
    incoming = {record["record_key"]: record for record in records}

    if incoming:
        taken = session.execute(
            select(AttendanceRecord.record_key, AttendanceRecord.source_file).where(
                AttendanceRecord.record_key.in_(list(incoming)),
                AttendanceRecord.is_active,
                AttendanceRecord.source_id != source_id,
            )
        ).all()
        for key, other_file in taken:
            record = incoming.pop(key)
            outcome.failures.append(
                RowFailure(
                    record["source_page_or_row"],
                    f"{record['employee_id']} on {record['attendance_date']} "
                    f"is already recorded in {other_file}",
                )
            )

    replaced: list[AttendanceRecord] = []
    to_insert: list[dict[str, Any]] = []
    for key, record in incoming.items():
        current = existing.get(key)
        if current is None:
            outcome.created += 1
            to_insert.append(record)
        elif _differs(current, record):
            outcome.updated += 1
            replaced.append(current)
            to_insert.append(record)
        else:
            outcome.unchanged += 1
    removed = [row for key, row in existing.items() if key not in incoming]
    outcome.removed = len(removed)

    for row in (*replaced, *removed):
        row.is_active = False
    session.flush()  # release the one-active-record-per-day index before inserting replacements

    new_rows = [
        AttendanceRecord(
            **record,
            source_id=source_id,
            source_version_id=version_id,
            source_file=source_file,
            is_active=True,
        )
        for record in to_insert
    ]
    session.add_all(new_rows)
    session.flush()
    by_key = {row.record_key: row for row in new_rows}
    for row in replaced:
        row.superseded_by = by_key[row.record_key].id
    return outcome


def _differs(current: AttendanceRecord, record: dict[str, Any]) -> bool:
    return any(getattr(current, name) != record.get(name) for name in COMPARED_FIELDS)
