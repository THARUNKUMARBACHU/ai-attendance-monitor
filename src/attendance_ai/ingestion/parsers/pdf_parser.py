"""PDF. Each page is read from its text layer when it has one (more than MIN_TEXT_CHARS characters) and
by OCR otherwise (see ocr.py).

Text pages (pdfplumber): every table whose header row names at least 3 known fields becomes rows. A table
continuing on a later page without a header keeps the columns of the one before, and a header row
repeated at the top of a page is not data. Rows are numbered 1-based per page, header excluded. Text
outside the tables becomes narrative, without the lines repeated on every page (running headers and
footers such as "Page 2 of 3").
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pdfplumber
from pdfplumber.page import Page
from pdfplumber.utils.exceptions import PdfminerException

from attendance_ai.ingestion.parsers import (
    CorruptFileError,
    find_header,
    is_record,
    repeats_header,
    row_values,
)
from attendance_ai.ingestion.parsers.ocr import read_scanned_pages
from attendance_ai.ingestion.types import ExtractedRow, ExtractedText, Locator, ParseResult

MIN_TEXT_CHARS = 20
_TEXT_TABLES = {"vertical_strategy": "text", "horizontal_strategy": "text"}
_PAGE_NUMBER = re.compile(
    r"^\W*(?:page\s*)?\d+(?:\s*(?:of|/)\s*\d+)?\W*$|\bpage\s+\d+\s*(?:of|/)\s*\d+\b", re.I
)


@dataclass(frozen=True, slots=True)
class _Header:
    columns: dict[int, str]
    width: int


@dataclass(frozen=True, slots=True)
class _Line:
    text: str
    top: float
    bottom: float


@dataclass(slots=True)
class _TextPage:
    rows: list[ExtractedRow] = field(default_factory=list)
    lines: list[_Line] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def parse_pdf(path: Path, *, tesseract_cmd: str | None = None) -> ParseResult:
    text_pages, scanned = _read_text_layer(path)
    scans = read_scanned_pages(path, scanned, tesseract_cmd=tesseract_cmd) if scanned else {}
    repeated = _repeated_lines(list(text_pages.values()))
    result = ParseResult()
    for page_no in sorted([*text_pages, *scans]):
        if page_no in scans:
            scan = scans[page_no]
            result.rows.extend(scan.rows)
            result.texts.extend(scan.texts)
            result.warnings.extend(scan.warnings)
            continue
        page = text_pages[page_no]
        result.rows.extend(page.rows)
        result.warnings.extend(page.warnings)
        lines = [
            line
            for line in page.lines
            if _key(line.text) not in repeated and not _PAGE_NUMBER.search(line.text)
        ]
        result.texts.extend(
            ExtractedText(text, Locator("page_text", page=page_no)) for text in _paragraphs(lines)
        )
    return result


def _read_text_layer(path: Path) -> tuple[dict[int, _TextPage], list[int]]:
    """The pages with a text layer, read, and the numbers of the pages that need OCR."""
    text_pages: dict[int, _TextPage] = {}
    scanned: list[int] = []
    header: _Header | None = None
    try:
        with pdfplumber.open(path) as pdf:
            for page_no, page in enumerate(pdf.pages, start=1):
                if len((page.extract_text() or "").strip()) <= MIN_TEXT_CHARS:
                    scanned.append(page_no)
                    continue
                text_pages[page_no], header = _read_text_page(page, page_no, header)
    except PdfminerException as exc:
        raise CorruptFileError(f"The file is not a readable PDF: {exc}") from exc
    return text_pages, scanned


def _read_text_page(page: Page, page_no: int, header: _Header | None) -> tuple[_TextPage, _Header | None]:
    """Rows from the page's tables (ruled tables; failing those, tables laid out by text alignment, which
    must then have their own header row) and the lines of text outside them."""
    result = _TextPage()
    ruled = page.find_tables()
    tables = ruled or page.find_tables(_TEXT_TABLES)
    rest = page
    row_no = 0
    for table in tables:
        rows = [[_join_lines(cell) for cell in row] for row in table.extract()]
        found = find_header(rows)
        if found is not None:
            header = _Header(found[1], len(rows[found[0]]))
            body = rows[found[0] + 1 :]
        elif ruled and header is not None and rows and len(rows[0]) == header.width:
            body = rows  # the table continues from the previous page
        else:
            continue
        rest = rest.outside_bbox(table.bbox, strict=False)
        for cells in body:
            if repeats_header(cells, header.columns):
                continue
            row_no += 1
            values = row_values(cells, header.columns)
            if is_record(values):
                locator = Locator("page_row", page=page_no, row=row_no)
                result.rows.append(ExtractedRow(values, locator, "pdf_text"))
    if not result.rows:
        result.warnings.append(f"page {page_no}: no attendance table found")
    result.lines = [_Line(line["text"].strip(), line["top"], line["bottom"]) for line in _text_lines(rest)]
    return result, header


def _text_lines(page: Page) -> list[dict[str, Any]]:
    return [line for line in page.extract_text_lines(return_chars=False) if line["text"].strip()]


def _join_lines(cell: str | None) -> str | None:
    """A table cell's text with wrapped lines joined by spaces."""
    if cell is None:
        return None
    return " ".join(part.strip() for part in cell.splitlines() if part.strip())


def _key(text: str) -> str:
    """Compare running headers and footers ignoring page numbers, case and spacing."""
    return re.sub(r"\s+", " ", re.sub(r"\d+", "#", text.lower())).strip()


def _repeated_lines(pages: Sequence[_TextPage]) -> set[str]:
    """Lines found on every text page (only meaningful from two pages up)."""
    if len(pages) < 2:
        return set()
    counts = Counter(key for page in pages for key in {_key(line.text) for line in page.lines})
    return {key for key, count in counts.items() if count == len(pages)}


def _paragraphs(lines: list[_Line]) -> list[str]:
    """Consecutive lines separated by less than 0.8 line heights form a paragraph."""
    paragraphs: list[list[str]] = []
    previous: _Line | None = None
    for line in lines:
        height = line.bottom - line.top
        if previous is not None and line.top - previous.bottom < 0.8 * height:
            paragraphs[-1].append(line.text)
        else:
            paragraphs.append([line.text])
        previous = line
    return [" ".join(parts) for parts in paragraphs]
