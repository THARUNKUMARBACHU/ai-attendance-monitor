from datetime import date, time
from decimal import Decimal

import pytest

from attendance_ai.core.directory import Directory
from attendance_ai.ingestion.normalize import (
    Normaliser,
    map_status,
    parse_date,
    parse_hours,
    parse_time,
    record_key,
)
from attendance_ai.ingestion.types import ExtractedRow, Locator


def _normaliser(directory: Directory) -> Normaliser:
    ctx = directory.system_context(
        tenant_id="acme", product_id="hrms", module="attendance", request_id="job_1"
    )
    tenant = directory.get_tenant("acme")
    assert tenant is not None
    return Normaliser(ctx, tenant)


def _row(
    row: int, method: str = "native_csv", confidence: dict[str, float] | None = None, **values: str | None
) -> ExtractedRow:
    return ExtractedRow(
        values=dict(values),
        locator=Locator("row", row=row),
        method=method,  # type: ignore[arg-type]
        confidence=confidence or {},
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-08-03", date(2026, 8, 3)),
        ("05/08/2026", date(2026, 8, 5)),
        ("05 Aug 2026", date(2026, 8, 5)),
        ("05-Aug-2026", date(2026, 8, 5)),
        ("5 August 2026", date(2026, 8, 5)),
        ("Aug 5, 2026", date(2026, 8, 5)),
        ("31/02/2026", None),
        ("yesterday", None),
    ],
)
def test_parse_date(raw: str, expected: date | None) -> None:
    assert parse_date(raw, "DD/MM/YYYY") == expected


def test_parse_date_respects_month_first_format() -> None:
    assert parse_date("05/08/2026", "MM/DD/YYYY") == date(2026, 5, 8)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("09:05", time(9, 5)), ("9:05 AM", time(9, 5)), ("12:30 pm", time(12, 30)), ("6:10 PM", time(18, 10))],
)
def test_parse_time(raw: str, expected: time) -> None:
    assert parse_time(raw) == expected


def test_parse_time_rejects_nonsense() -> None:
    assert parse_time("25:61") is None
    assert parse_time("0942") is None


def test_parse_hours_and_status_codes() -> None:
    assert parse_hours("9.15") == Decimal("9.15")
    assert parse_hours("8:30") == Decimal("8.50")
    codes = {"P": "PRESENT", "Half Day": "HALF_DAY", "WFH": "WFH"}
    assert map_status("P", codes) == "PRESENT"
    assert map_status("half day", codes) == "HALF_DAY"
    assert map_status("LEAVE", codes) == "LEAVE"
    assert map_status("???", codes) is None


def test_record_key_matches_ground_truth_convention() -> None:
    # Acme E001 on 2026-08-03 is the first record of the ground truth.
    assert record_key("acme", "hrms", "attendance", "E001", date(2026, 8, 3)) == "c1075b483f65169c"


def test_directory_is_the_source_of_truth_for_people(directory: Directory) -> None:
    batch = _normaliser(directory).run(
        [
            _row(
                2,
                employee_id="e001",
                employee_name="Rahul Sharma",
                department="Engineering",
                date="2026-08-03",
                status="Present",
                check_in="09:15",
                check_out="18:24",
                total_hours="9.15",
            )
        ]
    )
    (record,) = batch.records
    assert record["employee_id"] == "E001"
    assert record["entity_id"] == "ENG" and record["department"] == "Engineering"
    assert record["total_hours"] == Decimal("9.15")
    assert record["review_status"] == "auto_accepted"
    assert record["validation_flags"] == []
    assert record["source_page_or_row"] == "row 2"


def test_rows_that_cannot_be_trusted_are_rejected_with_reasons(directory: Directory) -> None:
    batch = _normaliser(directory).run(
        [
            _row(2, employee_id="E999", date="2026-08-03", status="Present"),
            _row(3, employee_id="E001", date="not a date", status="Present"),
            _row(4, employee_id="E001", date="2026-08-03", status="Zzz"),
            _row(5, employee_id="E001", date="2026-08-03", status="Present"),
            _row(6, employee_id="E001", date="2026-08-03", status="Present"),
        ]
    )
    reasons = {failure.location: failure.reason for failure in batch.failures}
    assert "unknown employee" in reasons["row 2"]
    assert "invalid date" in reasons["row 3"]
    assert "unknown status code" in reasons["row 4"]
    assert "duplicate row" in reasons["row 6"]
    assert len(batch.records) == 1


def test_low_confidence_ocr_values_become_needs_review_not_facts(directory: Directory) -> None:
    batch = _normaliser(directory).run(
        [
            _row(
                7,
                method="ocr",
                confidence={"employee_id": 0.95, "date": 0.96, "status": 0.41},
                employee_id="E019",
                date="11/08/2026",
                status="H0",
            )
        ]
    )
    (record,) = batch.records
    assert record["review_status"] == "needs_review"
    assert record["status"] is None  # unreadable, kept only for review
    assert record["extraction_confidence"] == Decimal("0.410")
    assert batch.needs_review == 1


def test_sensitive_remarks_are_restricted(directory: Directory) -> None:
    batch = _normaliser(directory).run(
        [
            _row(
                2, employee_id="E003", date="2026-08-18", status="Leave", remarks="Sick leave - viral fever"
            ),
            _row(
                3,
                employee_id="E002",
                date="2026-08-27",
                status="Leave",
                remarks="reachable on +91-98765-43210",
            ),
            _row(4, employee_id="E001", date="2026-08-04", status="Present", remarks="Client visit - Pune"),
        ]
    )
    by_employee = {record["employee_id"]: record for record in batch.records}
    assert (by_employee["E003"]["remarks_sensitivity"], by_employee["E003"]["pii_types"]) == (
        "restricted",
        ["medical"],
    )
    assert by_employee["E002"]["pii_types"] == ["phone"]
    assert by_employee["E001"]["remarks_sensitivity"] == "confidential"


def test_inconsistent_hours_are_flagged(directory: Directory) -> None:
    batch = _normaliser(directory).run(
        [
            _row(
                2,
                employee_id="E001",
                date="2026-08-03",
                status="Present",
                check_in="09:00",
                check_out="18:00",
                total_hours="7",
            )
        ]
    )
    assert batch.records[0]["total_hours"] == Decimal("9.00")
    assert "hours_mismatch" in batch.records[0]["validation_flags"]
