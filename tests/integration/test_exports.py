"""Exports against PostgreSQL: the same permitted records in JSON, XLSX and PDF, re-checked under the
caller's current access (tenant, role, entity and remark masking), with an audit event per export."""

import io
import json
from collections.abc import Callable
from typing import Any

import openpyxl
import pdfplumber
import pytest
from sqlalchemy import select

from attendance_ai.core.access import AccessContext
from attendance_ai.core.directory import Directory
from attendance_ai.core.errors import NotFoundError
from attendance_ai.exports.service import ExportService
from attendance_ai.generation.mock import MockProvider
from attendance_ai.governance.audit import AuditLog
from attendance_ai.orchestration.models import QueryRequest
from attendance_ai.stores.db import Database
from attendance_ai.stores.models import AuditEntry
from attendance_ai.stores.records import RecordFilter

from .test_answer_service import PCT, ScriptedLLM, _plan, _service

Ctx = Callable[[str], AccessContext]


def _exports(database: Database, directory: Directory) -> ExportService:
    return ExportService(database=database, directory=directory, audit=AuditLog(database), row_cap=10_000)


def _parse(fmt: str, content: bytes) -> list[dict[str, Any]]:
    """(record key, source file, source location, remarks) of every exported record."""
    if fmt == "json":
        return [
            {k: r[k] for k in ("record_key", "source_file", "source_page_or_row", "remarks")}
            for r in json.loads(content)["records"]
        ]
    if fmt == "xlsx":
        sheet = openpyxl.load_workbook(io.BytesIO(content))["Records"]
        header = [cell.value for cell in sheet[1]]
        columns = {
            "record_key": "Record key",
            "source_file": "Source file",
            "source_page_or_row": "Source location",
            "remarks": "Remarks",
        }
        return [
            {key: row[header.index(label)] for key, label in columns.items()}
            for row in sheet.iter_rows(min_row=2, values_only=True)
        ]
    raise ValueError(fmt)


def _reference(row: dict[str, Any]) -> tuple[Any, Any, Any]:
    return row["record_key"], row["source_file"], row["source_page_or_row"]


def _pdf_text(content: bytes) -> str:
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        return "".join(page.extract_text() or "" for page in pdf.pages)


@pytest.fixture
def files(add_records: Any) -> dict[str, str]:
    acme = add_records(
        "acme",
        [
            {
                "entity_id": "ENG",
                "employee_id": "E002",
                "remarks": "Sick leave - viral fever",
                "remarks_sensitivity": "restricted",
            },
            {"entity_id": "SAL", "department": "Sales", "employee_id": "E007"},
        ],
    )
    globex = add_records("globex", [{"entity_id": "ENG", "employee_id": "E002"}])
    return {"acme": acme, "globex": globex}


def test_every_format_holds_the_same_permitted_records_and_source_references(
    database: Database, directory: Directory, ctx_for: Ctx, files: dict[str, str]
) -> None:
    service = _exports(database, directory)
    admin = ctx_for("acme.admin")
    only = RecordFilter(source_file=files["acme"])
    json_rows = _parse("json", service.export(admin, fmt="json", filters=only).content)
    xlsx_rows = _parse("xlsx", service.export(admin, fmt="xlsx", filters=only).content)
    assert len(json_rows) == 2
    assert {_reference(r) for r in json_rows} == {_reference(r) for r in xlsx_rows}
    text = _pdf_text(service.export(admin, fmt="pdf", filters=only).content)
    assert all(row["record_key"] in text for row in json_rows)
    assert "fever" in text  # the admin's clearance covers restricted remarks


def test_exports_never_contain_another_tenants_records(
    database: Database, directory: Directory, ctx_for: Ctx, files: dict[str, str]
) -> None:
    service = _exports(database, directory)
    admin = ctx_for("acme.admin")
    targeted = service.export(admin, fmt="json", filters=RecordFilter(source_file=files["globex"]))
    assert json.loads(targeted.content)["records"] == []
    everything = json.loads(service.export(admin, fmt="json").content)["records"]
    assert all(row["source_file"] != files["globex"] for row in everything)


def test_exports_apply_the_callers_role_entity_and_clearance(
    database: Database, directory: Directory, ctx_for: Ctx, files: dict[str, str]
) -> None:
    service = _exports(database, directory)
    only = RecordFilter(source_file=files["acme"])
    manager = ctx_for("acme.eng.manager")  # Engineering only, confidential clearance
    for fmt in ("json", "xlsx"):
        rows = _parse(fmt, service.export(manager, fmt=fmt, filters=only).content)
        assert len(rows) == 1 and rows[0]["remarks"] == "[restricted]", fmt
    text = _pdf_text(service.export(manager, fmt="pdf", filters=only).content)
    assert "[restricted]" in text and "fever" not in text
    employee = ctx_for("acme.employee")  # E006: none of these records are theirs
    assert json.loads(service.export(employee, fmt="json", filters=only).content)["records"] == []


def test_each_export_is_audited(
    database: Database, directory: Directory, ctx_for: Ctx, files: dict[str, str]
) -> None:
    admin = ctx_for("acme.admin")
    _exports(database, directory).export(admin, fmt="xlsx", filters=RecordFilter(source_file=files["acme"]))
    with database.session_for(admin) as session:
        events = session.scalars(
            select(AuditEntry).where(
                AuditEntry.request_id == admin.request_id, AuditEntry.event_type == "export"
            )
        ).all()
        assert [(e.action, e.details["format"], e.details["record_count"]) for e in events] == [
            ("records_exported", "xlsx", 2)
        ]


def test_a_question_exports_its_answer_and_the_evidence_the_caller_may_see(
    database: Database, directory: Directory, ctx_for: Ctx, add_records: Any
) -> None:
    name = add_records("acme", [{"status": "PRESENT"}, {"status": "ABSENT"}])
    llm = ScriptedLLM(
        [_plan(PCT.format(name=name))], [{"answer": "Attendance was 50.0% [D1].", "citations": ["D1"]}]
    )
    admin = ctx_for("acme.admin")
    answered = _service(database, directory, MockProvider(llm)).answer(
        admin, QueryRequest(question="Attendance?")
    )
    assert answered.outcome == "answered", answered

    service = _exports(database, directory)
    body = json.loads(service.export(ctx_for("acme.admin"), fmt="json", request_id=admin.request_id).content)
    assert body["query"]["answer"] == "Attendance was 50.0% [D1]."
    assert {row["source_file"] for row in body["records"]} == {name} and len(body["records"]) == 2
    assert body["result"] == [{"attendance_pct": 50.0, "counted_days": 2}]
    text = _pdf_text(service.export(ctx_for("acme.admin"), fmt="pdf", request_id=admin.request_id).content)
    assert "50.0" in text and name in text.replace("\n", "")

    # Row-level security on the query log: another user's question is not there for a manager,
    # and another tenant's question is not there for anyone.
    with pytest.raises(NotFoundError):
        service.export(ctx_for("acme.eng.manager"), fmt="json", request_id=admin.request_id)
    with pytest.raises(NotFoundError):
        service.export(ctx_for("globex.admin"), fmt="json", request_id=admin.request_id)
