"""Run guarded SQL for one caller, behind three independent barriers:

1. the SQL guard (retrieval/sql_guard.py) has already allowed only a read-only query on v_attendance;
2. the query runs as the read-only role, whose row-level security context is set by the app;
3. the query's ``v_attendance`` resolves to a CTE that filters the real view by the caller's scope,
   bound as parameters, and re-applies masking. Even if the session settings were changed, the
   bound filters would still hold.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, time
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from attendance_ai.core.access import AccessContext
from attendance_ai.retrieval.sql_guard import GuardedSql
from attendance_ai.stores.db import Database
from attendance_ai.stores.models import AttendanceRecord

logger = logging.getLogger(__name__)

_SCOPED_VIEW = """
    SELECT record_id, attendance_date, employee_id, employee_name, department_id, department_name, status,
           check_in, check_out, total_hours, is_late, attendance_weight, is_counted,
           CASE WHEN remarks_restricted AND :_clearance < 3 THEN '[restricted]' ELSE remarks END AS remarks,
           source_file, source_page_or_row, extraction_method, extraction_confidence
    FROM public.v_attendance
    WHERE tenant_id = :_tenant_id AND product_id = :_product_id AND module = :_module
      AND (:_scope = 'tenant'
           OR (:_scope = 'department' AND department_id = ANY (string_to_array(:_entity_scope, ',')))
           OR (:_scope = 'self' AND employee_id = :_employee_id))
"""


class SqlExecutionError(RuntimeError):
    """The database rejected the query; the message is the first line of its error."""


@dataclass(frozen=True, slots=True)
class SqlResult:
    columns: list[str]
    rows: list[dict[str, Any]]
    truncated: bool
    evidence: list[dict[str, Any]] = field(default_factory=list)
    evidence_available: bool = False

    @property
    def is_empty(self) -> bool:
        """No rows, or a single aggregate row with nothing in it (for example a NULL percentage)."""
        if not self.rows:
            return True
        return len(self.rows) == 1 and all(value in (None, 0) for value in self.rows[0].values())


class SqlRunner:
    def __init__(self, database: Database, *, row_cap: int, evidence_cap: int) -> None:
        self._db = database
        self._row_cap = row_cap
        self._evidence_cap = evidence_cap

    def run(self, ctx: AccessContext, guarded: GuardedSql) -> SqlResult:
        params = _scope_params(ctx)
        try:
            with self._db.query_connection(ctx) as connection:
                result = connection.execute(text(_wrap(guarded.sql, self._row_cap + 1)), params)
                columns = list(result.keys())
                rows = [_json_row(row) for row in result.mappings()]
                evidence: list[dict[str, Any]] = []
                if guarded.evidence_sql:
                    evidence_result = connection.execute(
                        text(_wrap(guarded.evidence_sql, self._evidence_cap)), params
                    )
                    evidence = [_json_row(row) for row in evidence_result.mappings()]
        except DBAPIError as exc:
            detail = str(exc.orig).strip().splitlines()[0] if exc.orig else type(exc).__name__
            raise SqlExecutionError(detail) from exc
        return SqlResult(
            columns=columns,
            rows=rows[: self._row_cap],
            truncated=len(rows) > self._row_cap,
            evidence=evidence,
            evidence_available=guarded.evidence_sql is not None,
        )

    def pending_review(
        self, ctx: AccessContext, span: tuple[date, date] | None, departments: set[str] | None = None
    ) -> int:
        """Records waiting for review in the same period and departments as the evidence (and within the
        caller's scope, through row-level security), so the answer can disclose what it had to leave out."""
        query = (
            select(func.count())
            .select_from(AttendanceRecord)
            .where(AttendanceRecord.is_active, AttendanceRecord.review_status == "needs_review")
        )
        if span is not None:
            query = query.where(AttendanceRecord.attendance_date.between(*span))
        if departments:
            query = query.where(AttendanceRecord.entity_id.in_(sorted(departments)))
        with self._db.session_for(ctx) as session:
            return int(session.scalar(query) or 0)


def _wrap(sql: str, limit: int) -> str:
    # Colons in the model's SQL (casts, time literals) must not be read as bind parameters.
    escaped = sql.replace(":", "\\:")
    return (
        f"WITH v_attendance AS MATERIALIZED ({_SCOPED_VIEW}) "  # noqa: S608 - guarded SQL, bound scope
        f"SELECT * FROM ({escaped}) AS guarded_query LIMIT {int(limit)}"
    )


def _scope_params(ctx: AccessContext) -> dict[str, Any]:
    return {
        "_tenant_id": ctx.tenant_id,
        "_product_id": ctx.product_id,
        "_module": ctx.module,
        "_scope": ctx.scope.value,
        "_entity_scope": ",".join(ctx.entity_scope),
        "_employee_id": ctx.employee_id or "",
        "_clearance": ctx.clearance,
    }


def _json_row(row: Any) -> dict[str, Any]:
    return {key: _json_value(value) for key, value in dict(row).items()}


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value) if value != value.to_integral_value() else int(value)
    if isinstance(value, time):
        return value.strftime("%H:%M")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bytes | bytearray | memoryview):
        return None
    return value if isinstance(value, str | int | float | bool) or value is None else str(value)
