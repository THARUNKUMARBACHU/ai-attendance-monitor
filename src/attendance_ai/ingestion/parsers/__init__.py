"""File parsers: turn an uploaded file into raw attendance rows and narrative text blocks.

Parsers only extract. Values are the raw text found in the file (trimmed, empty cells as None; spreadsheet
dates as ISO), each row carries the exact place it came from, and the normaliser turns rows into records.
The helpers below are shared by the table-based parsers: a header row is the first row where at least
MIN_HEADER_FIELDS cells name a known field (see attendance_ai.ingestion.fields).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from attendance_ai.ingestion.fields import canonical_field
from attendance_ai.ingestion.types import MEDIA_CSV, MEDIA_DOCX, MEDIA_PDF, MEDIA_XLSX, ParseResult

MIN_HEADER_FIELDS = 3
HEADER_SEARCH_ROWS = 50


class UnsupportedFileError(ValueError):
    """There is no parser for this media type."""


class CorruptFileError(ValueError):
    """The file cannot be read as the format its media type claims."""


class OcrUnavailableError(RuntimeError):
    """A page needs OCR but the Tesseract executable cannot be run."""


def parse_file(path: Path, media_type: str, *, tesseract_cmd: str | None = None) -> ParseResult:
    """Parse one file of the given media type. ``tesseract_cmd`` is the Tesseract executable used for
    PDF pages without a text layer (default: ``tesseract`` on the PATH)."""
    # The format modules are imported on demand: the PDF and OCR stack is heavy.
    if media_type == MEDIA_CSV:
        from attendance_ai.ingestion.parsers.csv_parser import parse_csv

        return parse_csv(path)
    if media_type == MEDIA_XLSX:
        from attendance_ai.ingestion.parsers.xlsx_parser import parse_xlsx

        return parse_xlsx(path)
    if media_type == MEDIA_DOCX:
        from attendance_ai.ingestion.parsers.docx_parser import parse_docx

        return parse_docx(path)
    if media_type == MEDIA_PDF:
        from attendance_ai.ingestion.parsers.pdf_parser import parse_pdf

        return parse_pdf(path, tesseract_cmd=tesseract_cmd)
    raise UnsupportedFileError(f"No parser for media type {media_type!r}.")


def clean(value: object) -> str | None:
    """A cell's text with surrounding whitespace removed; None for an empty cell."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def header_columns(cells: Sequence[object]) -> dict[int, str]:
    """Column index -> canonical field for the cells of a candidate header row. Text cells only; when two
    columns name the same field, the first one wins."""
    columns: dict[int, str] = {}
    for index, cell in enumerate(cells):
        name = canonical_field(cell) if isinstance(cell, str) else None
        if name is not None and name not in columns.values():
            columns[index] = name
    return columns


def find_header(rows: Sequence[Sequence[object]]) -> tuple[int, dict[int, str]] | None:
    """The index and column map of the first header row among the first HEADER_SEARCH_ROWS rows."""
    for index, cells in enumerate(rows[:HEADER_SEARCH_ROWS]):
        columns = header_columns(cells)
        if len(columns) >= MIN_HEADER_FIELDS:
            return index, columns
    return None


def repeats_header(cells: Sequence[object], columns: Mapping[int, str]) -> bool:
    """True for a copy of the header row inside the data (for example repeated at the top of a page)."""
    same = 0
    for index, name in columns.items():
        cell = cells[index] if index < len(cells) else None
        if isinstance(cell, str) and canonical_field(cell) == name:
            same += 1
    return same >= MIN_HEADER_FIELDS


def row_values(cells: Sequence[object], columns: Mapping[int, str]) -> dict[str, str | None]:
    """The raw text of each mapped column of a data row."""
    return {name: clean(cells[index]) if index < len(cells) else None for index, name in columns.items()}


def is_record(values: Mapping[str, str | None]) -> bool:
    """False for blank rows and for footer, legend and totals rows: rows with no employee ID and no date."""
    return bool(values.get("employee_id") or values.get("date"))
