"""What every file parser produces: rows of raw field values and blocks of narrative text, each tied to
the exact place in the source file it came from."""

from dataclasses import dataclass, field
from typing import Any, Literal

MEDIA_CSV = "text/csv"
MEDIA_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MEDIA_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
MEDIA_PDF = "application/pdf"

ExtractionMethod = Literal["native_csv", "native_xlsx", "native_docx", "pdf_text", "ocr"]
LocatorType = Literal["row", "cell", "table_row", "page_row", "paragraph", "page_text"]

# Canonical field names a parser may fill in ExtractedRow.values.
FIELDS: tuple[str, ...] = (
    "employee_id",
    "employee_name",
    "department",
    "date",
    "status",
    "check_in",
    "check_out",
    "total_hours",
    "remarks",
)


@dataclass(frozen=True, slots=True)
class Locator:
    """Where a value came from. Conventions (docs/design.md, section 5):
    CSV physical line (header = 1); XLSX sheet + cell; DOCX table + data row; PDF page + data row."""

    type: LocatorType
    row: int | None = None
    sheet: str | None = None
    cell: str | None = None
    table: int | None = None
    page: int | None = None
    paragraph: int | None = None

    def as_dict(self) -> dict[str, Any]:
        items = (
            ("type", self.type),
            ("sheet", self.sheet),
            ("cell", self.cell),
            ("table", self.table),
            ("page", self.page),
            ("paragraph", self.paragraph),
            ("row", self.row),
        )
        return {key: value for key, value in items if value is not None}

    def describe(self) -> str:
        match self.type:
            case "row":
                return f"row {self.row}"
            case "cell":
                return f"sheet '{self.sheet}', cell {self.cell}"
            case "table_row":
                return f"table {self.table}, row {self.row}"
            case "page_row":
                return f"page {self.page}, row {self.row}"
            case "paragraph":
                return f"paragraph {self.paragraph}"
            case "page_text":
                return f"page {self.page}"


@dataclass(frozen=True, slots=True)
class ExtractedRow:
    """One attendance row as found in the file. Values are raw text keyed by canonical field name;
    spreadsheet dates are given as ISO YYYY-MM-DD. Confidence is per field in 0..1; fields missing
    from ``confidence`` count as 1.0 (native, exact extraction)."""

    values: dict[str, str | None]
    locator: Locator
    method: ExtractionMethod
    confidence: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ExtractedText:
    """A block of narrative text (not part of an attendance table), with its section heading if any."""

    text: str
    locator: Locator
    heading: str | None = None


@dataclass(slots=True)
class ParseResult:
    rows: list[ExtractedRow] = field(default_factory=list)
    texts: list[ExtractedText] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
