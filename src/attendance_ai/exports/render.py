"""Render one export dataset as JSON, XLSX or PDF. Every format is built from the same records, so their
contents and source references are identical; only the layout differs."""

from __future__ import annotations

import io
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal
from xml.sax.saxutils import escape

from openpyxl import Workbook  # type: ignore[import-untyped]
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE  # type: ignore[import-untyped]
from openpyxl.styles import Alignment, Font, PatternFill  # type: ignore[import-untyped]
from openpyxl.utils import get_column_letter  # type: ignore[import-untyped]
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import LongTable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from attendance_ai.exports.datasets import RECORD_FIELDS, ExportDataset

Format = Literal["json", "xlsx", "pdf"]
MEDIA_TYPES: dict[str, str] = {
    "json": "application/json",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pdf": "application/pdf",
}


@dataclass(frozen=True, slots=True)
class RenderedExport:
    content: bytes
    media_type: str
    filename: str


def render(dataset: ExportDataset, fmt: Format) -> RenderedExport:
    builders = {"json": _json, "xlsx": _xlsx, "pdf": _pdf}
    return RenderedExport(builders[fmt](dataset), MEDIA_TYPES[fmt], _filename(dataset, fmt))


def _filename(dataset: ExportDataset, fmt: str) -> str:
    stamp = re.sub(r"[^0-9]", "", str(dataset.meta.get("generated_at", "")))[:14]
    subject = dataset.meta.get("request_id") if dataset.kind == "query" else dataset.meta.get("tenant_id")
    name = f"attendance-{dataset.kind}_{subject}_{stamp[:8]}-{stamp[8:]}.{fmt}"
    return re.sub(r"[^A-Za-z0-9._-]", "_", name)


# --- JSON -------------------------------------------------------------------------------------------


def _json(dataset: ExportDataset) -> bytes:
    body: dict[str, Any] = {"export": dataset.meta}
    if dataset.query is not None:
        body["query"] = dataset.query
        body["result"] = dataset.result
    body["records"] = dataset.records
    return json.dumps(body, ensure_ascii=False, indent=2, default=str).encode("utf-8")


# --- XLSX -------------------------------------------------------------------------------------------

_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")
_HEADER_FONT = Font(bold=True, color="FFFFFF")
_HEADER_FILL = PatternFill("solid", fgColor="0F6E6E")
_WRAP = Alignment(vertical="top", wrap_text=True)


def excel_value(value: Any) -> Any:
    """A cell value that a spreadsheet will never evaluate: text that looks like a formula is prefixed
    with an apostrophe, and characters Excel cannot store are removed."""
    if isinstance(value, Mapping | list | tuple):
        value = json.dumps(value, ensure_ascii=False, default=str)
    if isinstance(value, str):
        value = ILLEGAL_CHARACTERS_RE.sub("", value)
        if value.startswith(_FORMULA_START):
            return "'" + value
    return value


def _xlsx(dataset: ExportDataset) -> bytes:
    workbook = Workbook()
    about = workbook.active
    about.title = "About"
    _key_values(about, dataset.meta)
    first = about
    if dataset.query is not None:
        answer = workbook.create_sheet("Answer")
        _key_values(answer, {key: value for key, value in dataset.query.items() if key != "citations"})
        first = answer
        citations = workbook.create_sheet("Citations")
        _table(citations, _CITATION_FIELDS, dataset.query.get("citations") or [])
        if dataset.result:
            columns = tuple((name, name.replace("_", " ").capitalize()) for name in dataset.result[0])
            _table(workbook.create_sheet("Result"), columns, dataset.result)
    records = workbook.create_sheet("Records")
    _table(records, RECORD_FIELDS, dataset.records)
    workbook.active = workbook.index(first if dataset.query is not None else records)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


_CITATION_FIELDS: tuple[tuple[str, str], ...] = (
    ("id", "Citation"),
    ("kind", "Kind"),
    ("source_file", "Source file"),
    ("locations", "Locations"),
    ("record_count", "Records"),
    ("snippet", "Snippet"),
)


def _key_values(sheet: Any, values: Mapping[str, Any]) -> None:
    sheet.append(["Field", "Value"])
    for cell in sheet[1]:
        cell.font, cell.fill = _HEADER_FONT, _HEADER_FILL
    for key, value in values.items():
        sheet.append([key.replace("_", " ").capitalize(), excel_value(value)])
    for row in sheet.iter_rows(min_row=2):
        row[0].font = Font(bold=True)
        row[1].alignment = _WRAP
    sheet.column_dimensions["A"].width = 22
    sheet.column_dimensions["B"].width = 100


def _table(sheet: Any, fields: Sequence[tuple[str, str]], rows: Iterable[Mapping[str, Any]]) -> None:
    sheet.append([label for _, label in fields])
    for cell in sheet[1]:
        cell.font, cell.fill = _HEADER_FONT, _HEADER_FILL
    widths = [len(label) for _, label in fields]
    for row in rows:
        values = [excel_value(row.get(name)) for name, _ in fields]
        sheet.append(values)
        widths = [max(width, len(str(value or ""))) for width, value in zip(widths, values, strict=True)]
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = min(width + 2, 60)


# --- PDF --------------------------------------------------------------------------------------------

_ACCENT = colors.HexColor("#0F6E6E")
_RULE = colors.HexColor("#CBCAC2")
_ZEBRA = colors.HexColor("#F5F5F2")
_MUTED = colors.HexColor("#6B6A66")
_TITLE = ParagraphStyle("title", fontName="Helvetica-Bold", fontSize=14, leading=18, spaceAfter=4)
_HEADING = ParagraphStyle(
    "heading", fontName="Helvetica-Bold", fontSize=10, leading=13, spaceBefore=8, spaceAfter=4
)
_BODY = ParagraphStyle("body", fontName="Helvetica", fontSize=8, leading=10.5)
_CELL = ParagraphStyle("cell", fontName="Helvetica", fontSize=6.5, leading=8)
_MARGIN = 12 * mm

# (field, label, width in points). The widths fill a landscape A4 page inside the margins.
_PDF_COLUMNS: tuple[tuple[str, str, float], ...] = (
    ("attendance_date", "Date", 50),
    ("employee", "Employee", 92),
    ("department_name", "Department", 62),
    ("status", "Status", 52),
    ("check_in", "In", 30),
    ("check_out", "Out", 30),
    ("total_hours", "Hours", 30),
    ("remarks", "Remarks", 138),
    ("source", "Source", 142),
    ("extraction_confidence", "Conf.", 30),
    ("review_status", "Review", 50),
    ("record_key", "Record key", 66),
)
_WRAPPED = frozenset({"employee", "department_name", "remarks", "source"})


def pdf_text(value: Any) -> str:
    """Text for the PDF's markup: escaped (uploaded content can contain '<' or '&'), line breaks kept,
    and characters the built-in fonts cannot draw replaced by '?'."""
    text = "" if value is None else str(value)
    text = text.encode("cp1252", "replace").decode("cp1252")
    return escape(text).replace("\n", "<br/>")


def _pdf(dataset: ExportDataset) -> bytes:
    buffer = io.BytesIO()
    meta = dataset.meta
    document = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=_MARGIN,
        rightMargin=_MARGIN,
        topMargin=_MARGIN,
        bottomMargin=_MARGIN + 4 * mm,
        title=f"{dataset.title} - {meta.get('tenant_name', '')}",
        author=str(meta.get("generated_by", "")),
        subject="Attendance Intelligence export",
    )
    story: list[Any] = [Paragraph(pdf_text(dataset.title), _TITLE), _meta_table(meta), Spacer(1, 6)]
    if dataset.query is not None:
        story.extend(_query_section(dataset.query))
        if dataset.result:
            story.append(Paragraph("Result table", _HEADING))
            story.append(_result_table(dataset.result, document.width))
        story.append(Paragraph("Evidence records", _HEADING))
    else:
        story.append(Paragraph("Records", _HEADING))
    story.append(_records_table(dataset.records))

    def footer(canvas: Any, doc: Any) -> None:
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(_MUTED)
        line = (
            f"{meta.get('tenant_name', '')} - {str(meta.get('classification', '')).capitalize()} - "
            f"generated {meta.get('generated_at', '')} by {meta.get('generated_by', '')}"
        )
        canvas.drawString(doc.leftMargin, 8 * mm, line.encode("cp1252", "replace").decode("cp1252"))
        canvas.drawRightString(doc.pagesize[0] - doc.rightMargin, 8 * mm, f"Page {doc.page}")
        canvas.restoreState()

    document.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()


def _meta_table(meta: Mapping[str, Any]) -> Table:
    shown = (
        ("Tenant", meta.get("tenant_name")),
        ("Scope", meta.get("scope")),
        ("Generated", f"{meta.get('generated_at')} by {meta.get('generated_by')} ({meta.get('role')})"),
        ("Classification", meta.get("classification")),
        ("Filters", json.dumps(meta.get("filters") or {}, default=str) if "filters" in meta else None),
        ("Question ID", meta.get("request_id")),
        ("Records", f"{meta.get('record_count')}" + (" (truncated)" if meta.get("truncated") else "")),
        ("Versions", f"data {meta.get('data_version')}, knowledge {meta.get('knowledge_version')}"),
        ("Note", meta.get("note")),
    )
    rows = [
        [Paragraph(f"<b>{label}</b>", _BODY), Paragraph(pdf_text(value), _BODY)]
        for label, value in shown
        if value
    ]
    table = Table(rows, colWidths=[30 * mm, 200 * mm], hAlign="LEFT")
    table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), *_padding(1)]))
    return table


def _query_section(query: Mapping[str, Any]) -> list[Any]:
    confidence = query.get("confidence") or {}
    story: list[Any] = [
        Paragraph("Question", _HEADING),
        Paragraph(pdf_text(query.get("question")), _BODY),
        Paragraph("Answer", _HEADING),
        Paragraph(pdf_text(query.get("answer") or query.get("message") or "(no answer)"), _BODY),
    ]
    details = [
        f"Outcome: {query.get('outcome')}",
        f"Retrieval: {query.get('retrieval_mode') or '-'}",
        f"Confidence: {confidence.get('band', '-')} ({confidence.get('score', '-')})",
    ]
    story.append(Spacer(1, 3))
    story.append(Paragraph(pdf_text(" | ".join(details)), _BODY))
    for reason in confidence.get("reasons") or []:
        story.append(Paragraph(pdf_text(f"- {reason}"), _BODY))
    citations = query.get("citations") or []
    if citations:
        story.append(Paragraph("Citations", _HEADING))
        rows = [["ID", "Source file", "Locations", "Records"]] + [
            [
                str(c.get("id", "")),
                Paragraph(pdf_text(c.get("source_file")), _CELL),
                Paragraph(pdf_text(c.get("locations")), _CELL),
                str(c.get("record_count") or ""),
            ]
            for c in citations
        ]
        table = Table(rows, colWidths=[30, 220, 380, 50], repeatRows=1, hAlign="LEFT")
        table.setStyle(_grid_style())
        story.append(table)
    return story


def _result_table(result: Sequence[Mapping[str, Any]], width: float) -> Table:
    columns = list(result[0])
    rows = [[name.replace("_", " ") for name in columns]] + [
        [Paragraph(pdf_text(row.get(name)), _CELL) for name in columns] for row in result
    ]
    table = Table(rows, colWidths=[width / len(columns)] * len(columns), repeatRows=1, hAlign="LEFT")
    table.setStyle(_grid_style())
    return table


def _records_table(records: Sequence[Mapping[str, Any]]) -> Any:
    if not records:
        return Paragraph("No records.", _BODY)
    rows: list[list[Any]] = [[label for _, label, _ in _PDF_COLUMNS]]
    for record in records:
        values = {
            **record,
            "employee": f"{record.get('employee_id') or ''}\n{record.get('employee_name') or ''}",
            "source": f"{record.get('source_file') or ''}\n{record.get('source_page_or_row') or ''}",
        }
        rows.append(
            [
                Paragraph(pdf_text(values.get(name)), _CELL) if name in _WRAPPED else _short(values.get(name))
                for name, _, _ in _PDF_COLUMNS
            ]
        )
    table = LongTable(rows, colWidths=[width for _, _, width in _PDF_COLUMNS], repeatRows=1, hAlign="LEFT")
    table.setStyle(_grid_style())
    return table


def _short(value: Any) -> str:
    if value is None:
        return ""
    return str(value).encode("cp1252", "replace").decode("cp1252")


def _grid_style() -> TableStyle:
    return TableStyle(
        [
            ("BACKGROUND", (0, 0), (-1, 0), _ACCENT),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
            ("FONTSIZE", (0, 0), (-1, -1), 6.5),
            ("LEADING", (0, 0), (-1, -1), 8),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("GRID", (0, 0), (-1, -1), 0.25, _RULE),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, _ZEBRA]),
            *_padding(3),
        ]
    )


def _padding(points: float) -> list[tuple[Any, ...]]:
    return [
        ("LEFTPADDING", (0, 0), (-1, -1), points),
        ("RIGHTPADDING", (0, 0), (-1, -1), points),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]
