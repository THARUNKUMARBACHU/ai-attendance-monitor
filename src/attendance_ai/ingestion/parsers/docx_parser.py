"""DOCX (python-docx). Every table whose header row names at least 3 known fields becomes rows; tables are
numbered 1-based across the document and rows 1-based below the header. Body paragraphs outside tables
become narrative text under the most recent heading (a Heading style or the Title), numbered 1-based over
the non-empty body paragraphs, headings included."""

from __future__ import annotations

from pathlib import Path

import docx
from docx.table import Table
from docx.text.paragraph import Paragraph

from attendance_ai.ingestion.parsers import (
    CorruptFileError,
    find_header,
    is_record,
    repeats_header,
    row_values,
)
from attendance_ai.ingestion.types import ExtractedRow, ExtractedText, Locator, ParseResult


def parse_docx(path: Path) -> ParseResult:
    try:
        document = docx.Document(str(path))
    except Exception as exc:
        raise CorruptFileError(f"The file is not a readable Word document: {exc}") from exc
    result = ParseResult()
    heading: str | None = None
    table_no = paragraph_no = 0
    for block in document.iter_inner_content():
        if isinstance(block, Table):
            table_no += 1
            _table_rows(block, table_no, result)
            continue
        text = block.text.strip()
        if not text:
            continue
        paragraph_no += 1
        if _is_heading(block):
            heading = text
        else:
            result.texts.append(ExtractedText(text, Locator("paragraph", paragraph=paragraph_no), heading))
    if not result.rows:
        result.warnings.append("no attendance table found in the document")
    return result


def _table_rows(table: Table, table_no: int, result: ParseResult) -> None:
    rows = [[cell.text for cell in row.cells] for row in table.rows]
    header = find_header(rows)
    if header is None:
        return
    header_index, columns = header
    for row_no, cells in enumerate(rows[header_index + 1 :], start=1):
        if repeats_header(cells, columns):
            continue
        values = row_values(cells, columns)
        if is_record(values):
            locator = Locator("table_row", table=table_no, row=row_no)
            result.rows.append(ExtractedRow(values, locator, "native_docx"))


def _is_heading(paragraph: Paragraph) -> bool:
    style = paragraph.style
    name = (style.name or "").lower() if style is not None else ""
    return name == "title" or name.startswith("heading")
