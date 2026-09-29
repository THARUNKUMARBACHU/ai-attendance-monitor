"""Mapping from column headers found in real files to canonical field names."""

import re

HEADER_SYNONYMS: dict[str, tuple[str, ...]] = {
    "employee_id": ("employee id", "emp id", "emp code", "employee code", "staff no", "staff id", "emp no"),
    "employee_name": ("employee name", "name", "emp name", "staff name"),
    "department": ("department", "dept", "team"),
    "date": ("date", "attendance date", "date (dd/mm/yyyy)", "date (mm/dd/yyyy)", "date (yyyy-mm-dd)", "day"),
    "status": ("status", "status code", "attendance", "code"),
    "check_in": ("in time", "check-in", "check in", "in", "time in", "login"),
    "check_out": ("out time", "check-out", "check out", "out", "time out", "logout"),
    "total_hours": ("total hours", "hours", "hrs", "duration", "total hrs"),
    "remarks": ("remarks", "remark", "notes", "note", "comment", "comments", "reason"),
}

_LOOKUP = {synonym: name for name, synonyms in HEADER_SYNONYMS.items() for synonym in synonyms}


def normalise_header(header: str) -> str:
    return re.sub(r"\s+", " ", header.replace("\n", " ")).strip().strip(":").lower()


def canonical_field(header: str | None) -> str | None:
    """The canonical field for a column header, or None if the column is not recognised."""
    if header is None:
        return None
    return _LOOKUP.get(normalise_header(str(header)))
