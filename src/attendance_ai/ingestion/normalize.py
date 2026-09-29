"""Turn raw parsed rows into canonical attendance records, or into row failures with a reason.

The tenant's employee directory is the source of truth for names and departments. Values read by OCR
below the tenant's confidence threshold never become facts: the record is kept as "needs_review"
(excluded from answers) instead.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date, time
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from attendance_ai.core.access import AccessContext
from attendance_ai.core.directory import Employee, Tenant
from attendance_ai.core.domain import AttendanceStatus
from attendance_ai.governance.pii import classify_text
from attendance_ai.ingestion.types import ExtractedRow

KEY_FIELDS = ("employee_id", "date", "status", "check_in", "check_out")

_MONTHS = {
    name: number
    for number, names in enumerate(
        (
            ("jan", "january"),
            ("feb", "february"),
            ("mar", "march"),
            ("apr", "april"),
            ("may",),
            ("jun", "june"),
            ("jul", "july"),
            ("aug", "august"),
            ("sep", "sept", "september"),
            ("oct", "october"),
            ("nov", "november"),
            ("dec", "december"),
        ),
        start=1,
    )
    for name in names
}
_ISO_DATE = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")
_NUMERIC_DATE = re.compile(r"^(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})$")
_DAY_MONTH_YEAR = re.compile(r"^(\d{1,2})[\s-]+([A-Za-z]{3,9})\.?[\s,-]+(\d{4})$")
_MONTH_DAY_YEAR = re.compile(r"^([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})$")
_TIME = re.compile(r"^(?P<h>\d{1,2})[:.](?P<m>\d{2})(?::(?P<s>\d{2}))?\s*(?P<ampm>[AaPp]\.?[Mm]\.?)?$")
_HOURS_CLOCK = re.compile(r"^(\d{1,2}):(\d{2})$")
_TWO_PLACES = Decimal("0.01")


class RowRejectedError(ValueError):
    """The row cannot become a record; the message says why."""


@dataclass(frozen=True, slots=True)
class RowFailure:
    location: str
    reason: str


@dataclass(slots=True)
class NormalisedBatch:
    records: list[dict[str, Any]] = field(default_factory=list)
    failures: list[RowFailure] = field(default_factory=list)

    @property
    def needs_review(self) -> int:
        return sum(1 for record in self.records if record["review_status"] == "needs_review")


def record_key(tenant_id: str, product_id: str, module: str, employee_id: str, attendance_date: date) -> str:
    """The logical identity of one employee's attendance on one day."""
    raw = f"{tenant_id}|{product_id}|{module}|{employee_id}|{attendance_date.isoformat()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def parse_date(raw: str | None, date_format: str = "DD/MM/YYYY") -> date | None:
    if not raw:
        return None
    text = raw.strip()
    try:
        if match := _ISO_DATE.match(text):
            return date(int(match[1]), int(match[2]), int(match[3]))
        if match := _NUMERIC_DATE.match(text):
            first, second, year = int(match[1]), int(match[2]), int(match[3])
            month_first = date_format.upper().startswith("MM")
            day, month = (second, first) if month_first else (first, second)
            return date(year, month, day)
        if match := _DAY_MONTH_YEAR.match(text):
            named = _MONTHS.get(match[2].lower())
            return date(int(match[3]), named, int(match[1])) if named else None
        if match := _MONTH_DAY_YEAR.match(text):
            named = _MONTHS.get(match[1].lower())
            return date(int(match[3]), named, int(match[2])) if named else None
    except ValueError:
        return None
    return None


def parse_time(raw: str | None) -> time | None:
    if not raw:
        return None
    match = _TIME.match(raw.strip())
    if not match:
        return None
    hour, minute = int(match["h"]), int(match["m"])
    if ampm := match["ampm"]:
        if not 1 <= hour <= 12:
            return None
        is_pm = ampm[0].lower() == "p"
        hour = (hour % 12) + (12 if is_pm else 0)
    if hour > 23 or minute > 59:
        return None
    return time(hour, minute)


def parse_hours(raw: str | None) -> Decimal | None:
    if not raw:
        return None
    text = raw.strip()
    if match := _HOURS_CLOCK.match(text):
        return (Decimal(int(match[1])) + Decimal(int(match[2])) / 60).quantize(_TWO_PLACES, ROUND_HALF_UP)
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None
    return value.quantize(_TWO_PLACES, ROUND_HALF_UP) if value >= 0 else None


def map_status(raw: str | None, status_codes: dict[str, str]) -> str | None:
    if not raw or not raw.strip():
        return None
    text = raw.strip()
    if text in status_codes:
        return status_codes[text]
    folded = {code.casefold(): status for code, status in status_codes.items()}
    if text.casefold() in folded:
        return folded[text.casefold()]
    canonical = re.sub(r"[\s_-]+", "_", text).upper()
    return canonical if canonical in AttendanceStatus.__members__ else None


class Normaliser:
    """Normalises the rows of one uploaded file for one tenant."""

    def __init__(self, ctx: AccessContext, tenant: Tenant, *, classification: str = "confidential") -> None:
        self._ctx = ctx
        self._tenant = tenant
        self._classification = classification
        self._threshold = tenant.config.ocr_review_threshold
        self._status_codes = dict(tenant.config.status_codes)
        self._names = self._index_names(tenant)

    def run(self, rows: list[ExtractedRow]) -> NormalisedBatch:
        batch = NormalisedBatch()
        seen: dict[str, str] = {}
        for row in rows:
            location = row.locator.describe()
            try:
                record = self._normalise(row)
            except RowRejectedError as exc:
                batch.failures.append(RowFailure(location, str(exc)))
                continue
            key = record["record_key"]
            if key in seen:
                batch.failures.append(
                    RowFailure(
                        location,
                        f"duplicate row for {record['employee_id']} on {record['attendance_date']} "
                        f"(first seen at {seen[key]})",
                    )
                )
                continue
            seen[key] = location
            batch.records.append(record)
        return batch

    def _normalise(self, row: ExtractedRow) -> dict[str, Any]:
        values, confidence = row.values, row.confidence
        low = [name for name in KEY_FIELDS if confidence.get(name, 1.0) < self._threshold]
        flags: list[str] = []

        employee = self._resolve_employee(values.get("employee_id"), values.get("employee_name"), low)
        attendance_date = parse_date(values.get("date"), self._tenant.config.date_format)
        if attendance_date is None:
            raw = values.get("date")
            raise RowRejectedError(f"unreadable date {raw!r}" if "date" in low else f"invalid date {raw!r}")

        raw_status = (values.get("status") or "").strip() or None
        status = map_status(raw_status, self._status_codes)
        if status is None and "status" not in low:
            raise RowRejectedError(f"unknown status code {raw_status!r}" if raw_status else "missing status")

        check_in = self._time(values, "check_in", low, flags)
        check_out = self._time(values, "check_out", low, flags)
        total_hours = self._hours(values.get("total_hours"), check_in, check_out, flags)
        remarks = (values.get("remarks") or "").strip() or None
        sensitivity, pii_types = classify_text(remarks)
        self._check_labels(values, employee, low, flags)

        department = self._tenant.departments[employee.entity_id]
        key_confidences = [
            confidence.get(name, 1.0) for name in KEY_FIELDS if values.get(name) or name in confidence
        ]
        ctx = self._ctx
        return {
            "record_key": record_key(
                ctx.tenant_id, ctx.product_id, ctx.module, employee.employee_id, attendance_date
            ),
            "tenant_id": ctx.tenant_id,
            "product_id": ctx.product_id,
            "module": ctx.module,
            "entity_id": employee.entity_id,
            "classification": self._classification,
            "department": department.name,
            "employee_id": employee.employee_id,
            "employee_name": employee.name,
            "attendance_date": attendance_date,
            "status": status,
            "raw_status": raw_status,
            "check_in": check_in,
            "check_out": check_out,
            "total_hours": total_hours,
            "remarks": remarks,
            "remarks_sensitivity": sensitivity,
            "pii_types": pii_types,
            "source_locator": row.locator.as_dict(),
            "source_page_or_row": row.locator.describe(),
            "extraction_method": row.method,
            "extraction_confidence": Decimal(str(min(key_confidences, default=1.0))).quantize(
                Decimal("0.001")
            ),
            "field_confidence": {k: round(v, 3) for k, v in confidence.items()} or None,
            "review_status": "needs_review" if low else "auto_accepted",
            "validation_flags": flags,
        }

    def _resolve_employee(self, raw_id: str | None, raw_name: str | None, low: list[str]) -> Employee:
        if raw_id and raw_id.strip():
            employee = self._tenant.employees.get(raw_id.strip().upper())
            if employee:
                return employee
        if raw_name and raw_name.strip():
            matches = self._names.get(raw_name.strip().casefold(), [])
            if len(matches) == 1:
                return matches[0]
        if not raw_id:
            raise RowRejectedError("missing employee ID")
        prefix = "unreadable" if "employee_id" in low else "unknown"
        raise RowRejectedError(f"{prefix} employee {raw_id.strip()!r} (not in this tenant's directory)")

    def _time(
        self, values: dict[str, str | None], name: str, low: list[str], flags: list[str]
    ) -> time | None:
        raw = values.get(name)
        if not raw or not raw.strip():
            return None
        parsed = parse_time(raw)
        if parsed is None and name not in low:
            flags.append(f"invalid_{name}")
        return parsed

    @staticmethod
    def _hours(
        raw: str | None, check_in: time | None, check_out: time | None, flags: list[str]
    ) -> Decimal | None:
        computed: Decimal | None = None
        if check_in and check_out:
            minutes = (check_out.hour * 60 + check_out.minute) - (check_in.hour * 60 + check_in.minute)
            if minutes < 0:
                minutes += 24 * 60
                flags.append("overnight_shift")
            computed = (Decimal(minutes) / 60).quantize(_TWO_PLACES, ROUND_HALF_UP)
        given = parse_hours(raw)
        if raw and raw.strip() and given is None:
            flags.append("invalid_total_hours")
        if computed is not None and given is not None and abs(computed - given) > Decimal("0.05"):
            flags.append("hours_mismatch")
        return computed if computed is not None else given

    def _check_labels(
        self, values: dict[str, str | None], employee: Employee, low: list[str], flags: list[str]
    ) -> None:
        raw_department = (values.get("department") or "").strip().casefold()
        department = self._tenant.departments[employee.entity_id]
        if raw_department and raw_department not in {
            department.name.casefold(),
            department.entity_id.casefold(),
        }:
            flags.append("department_mismatch")
        raw_name = (values.get("employee_name") or "").strip().casefold()
        if raw_name and raw_name != employee.name.casefold() and "employee_id" not in low:
            flags.append("name_mismatch")

    @staticmethod
    def _index_names(tenant: Tenant) -> dict[str, list[Employee]]:
        names: dict[str, list[Employee]] = {}
        for employee in tenant.employees.values():
            names.setdefault(employee.name.casefold(), []).append(employee)
        return names
