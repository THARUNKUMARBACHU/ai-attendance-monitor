"""CSV: UTF-8 text (a byte-order mark is ignored), comma-separated; semicolons, tabs or pipes are tried
when commas do not give a header row. Each row's locator is its physical line number in the file, so the
header on the first line is line 1."""

from __future__ import annotations

import codecs
import csv
import io
import itertools
from pathlib import Path

from attendance_ai.ingestion.parsers import (
    HEADER_SEARCH_ROWS,
    CorruptFileError,
    find_header,
    is_record,
    repeats_header,
    row_values,
)
from attendance_ai.ingestion.types import ExtractedRow, Locator, ParseResult

DELIMITERS = (",", ";", "\t", "|")

_Record = tuple[int, list[str]]


def parse_csv(path: Path) -> ParseResult:
    result = ParseResult()
    text = _decode(path.read_bytes(), result.warnings)
    sniffed = _sniff(text)
    if sniffed is None:
        result.warnings.append("no attendance table found: no row names at least 3 known columns")
        return result

    delimiter, header_index, columns = sniffed
    for line_no, cells in _records(text, delimiter)[header_index + 1 :]:
        if repeats_header(cells, columns):
            continue
        values = row_values(cells, columns)
        if is_record(values):
            result.rows.append(ExtractedRow(values, Locator("row", row=line_no), "native_csv"))
    return result


def _sniff(text: str) -> tuple[str, int, dict[int, str]] | None:
    """The first delimiter that yields a header row, with that row's index and column map."""
    for delimiter in DELIMITERS:
        header = find_header([cells for _, cells in _records(text, delimiter, HEADER_SEARCH_ROWS)])
        if header is not None:
            return delimiter, *header
    return None


def _decode(data: bytes, warnings: list[str]) -> str:
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return data.decode("utf-16")
    if b"\x00" in data:
        raise CorruptFileError("The file contains binary data, not CSV text.")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        warnings.append("the file is not valid UTF-8; it was read as Windows-1252")
        return data.decode("cp1252", errors="replace")


def _records(text: str, delimiter: str, limit: int | None = None) -> list[_Record]:
    """(physical line number, cells) for each CSV record; a quoted field may span several lines."""
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    records: list[_Record] = []
    line_no = 1
    try:
        for cells in itertools.islice(reader, limit):
            records.append((line_no, cells))
            line_no = reader.line_num + 1
    except csv.Error as exc:
        raise CorruptFileError(f"The file is not valid CSV (line {reader.line_num}): {exc}") from exc
    return records
