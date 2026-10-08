"""OCR for PDF pages without a text layer, such as scanned registers.

Each page is rendered at 300 DPI, straightened (projection-profile deskew), cleaned of table ruling lines
and read by Tesseract twice, at two binarisation levels. The table is rebuilt from word positions: the
header line (column names matched to canonical fields, tolerating common OCR confusions) gives the column
boundaries, and each line below it is a row, until a caption such as "Status codes:" or "Note:" ends the
table. Captions and notes below the table become narrative text; titles, page numbers and "Verified by"
lines are dropped.

A field's confidence is the lowest word confidence in its cell (noise fragments included). It is capped
at REVIEW_CAP when the two reads disagree, when the text cannot be a value of that field, when the words
are split by a gap as wide as a column, or when the cell holds smudged or faint ink that did not print as
crisp strokes. An empty cell scores 1.0 only when it is blank paper.
"""

from __future__ import annotations

import difflib
import itertools
import math
import os
import re
import statistics
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path

import pypdfium2 as pdfium  # type: ignore[import-untyped]
import pytesseract  # type: ignore[import-untyped]
from PIL import Image, ImageChops, ImageFilter, ImageOps

from attendance_ai.ingestion.fields import HEADER_SYNONYMS, canonical_field
from attendance_ai.ingestion.parsers import MIN_HEADER_FIELDS, OcrUnavailableError, is_record
from attendance_ai.ingestion.types import ExtractedRow, ExtractedText, Locator

DPI = 300
MAX_SKEW_DEGREES = 3.0
RULE_RADIUS = 80  # px: a ruling line is an unbroken run of ink at least 2 * RULE_RADIUS + 1 px long
CUTS = (0.4, 0.3)  # binarisation levels, as the fraction of the ink contrast below the paper level
PAPER_MARGIN = 40  # grey levels this close to the paper level count as paper
MIN_INK_PIXELS = 500  # darker pixels than this on a page, or it is blank
REVIEW_CAP = 0.5
MAX_WORKERS = 4
TESSERACT_TIMEOUT = 180  # seconds per Tesseract run
# Pages are read in parallel, one Tesseract process each. Tesseract's own OpenMP threads on top of that
# oversubscribe the CPUs: on 8 cores the five-page sample scan took 169 s instead of about 30 s, and on a
# single CPU it timed out. One thread per process; set OMP_THREAD_LIMIT yourself to override.
os.environ.setdefault("OMP_THREAD_LIMIT", "1")
_TESSERACT_CONFIG = f"--psm 6 --dpi {DPI}"
_LINE_CONFIG = f"--psm 7 --dpi {DPI}"

_Box = tuple[int, int, int, int]

_CONFUSABLE = str.maketrans({"0": "o", "1": "i", "l": "i", "|": "i", "!": "i", "5": "s"})
_TIME = re.compile(r"\d{1,2}[:.]\d{2}(?:\s?[AaPp]\.?[Mm]\.?)?")
_PLAUSIBLE: dict[str, re.Pattern[str]] = {
    "employee_id": re.compile(r"(?=\S*\d)[A-Za-z0-9][A-Za-z0-9/_.-]*"),
    "employee_name": re.compile(r"[^\W\d_]+(?:[ '.-]+[^\W\d_]+)*\.?"),
    "date": re.compile(
        r"\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}|\d{4}-\d{1,2}-\d{1,2}|\d{1,2}[ -][A-Za-z]{3,9}[ ,-]+\d{2,4}"
    ),
    "status": re.compile(r"[^\W\d_]+(?:[ /-][^\W\d_]+)*"),
    "check_in": _TIME,
    "check_out": _TIME,
    "total_hours": re.compile(r"\d{1,2}(?:[.:]\d{1,2})?"),
}
_CAPTION = re.compile(r"^\W*[A-Za-z][A-Za-z .()/&-]{0,40}:(?:\s|$)")
_SKIPPED_LINE = re.compile(r"\bpage\s+\d+(?:\s*(?:of|/)\s*\d+)?\b|\bverified\s+by\b", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class ScannedPage:
    rows: list[ExtractedRow]
    texts: list[ExtractedText]
    warnings: list[str]


@dataclass(frozen=True, slots=True)
class _Word:
    text: str
    conf: float
    left: int
    top: int
    right: int
    bottom: int

    @property
    def cx(self) -> float:
        return (self.left + self.right) / 2

    @property
    def cy(self) -> float:
        return (self.top + self.bottom) / 2

    @property
    def is_noise(self) -> bool:
        return not any(char.isalnum() for char in self.text)


@dataclass(slots=True)
class _Line:
    words: list[_Word]
    cy: float

    @property
    def text(self) -> str:
        return " ".join(word.text for word in self.words)


@dataclass(frozen=True, slots=True)
class _HeaderCell:
    name: str | None
    words: tuple[_Word, ...]

    @property
    def left(self) -> int:
        return self.words[0].left

    @property
    def right(self) -> int:
        return self.words[-1].right


@dataclass(frozen=True, slots=True)
class _Column:
    name: str | None
    start: float
    end: float


@dataclass(frozen=True, slots=True)
class _Cell:
    text: str | None
    conf: float
    split: bool  # words separated by a column-wide gap: a column boundary was probably missed


_BLANK = _Cell(None, 1.0, split=False)


@dataclass(slots=True)
class _Scan:
    """One Tesseract read of a page."""

    lines: list[_Line]
    height: float  # typical text height in px


@dataclass(slots=True)
class _Page:
    """A straightened page without ruling lines, its binarised versions and ink masks."""

    gray: Image.Image
    binaries: list[Image.Image]
    black: Image.Image  # ink at the first binarisation level
    far_grey: Image.Image  # grey ink away from any crisp stroke: smudges, faint print
    frame: _Box | None  # extent of the vertical ruling lines (the table frame)


def read_scanned_pages(
    path: Path, page_numbers: Sequence[int], *, tesseract_cmd: str | None = None
) -> dict[int, ScannedPage]:
    """OCR the given 1-based pages of a PDF. Pages are rendered one at a time (pdfium is not thread-safe)
    and read in parallel, with at most a few rendered pages held in memory."""
    if tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
    _check_tesseract()
    results: dict[int, ScannedPage] = {}
    workers = max(1, min(MAX_WORKERS, os.cpu_count() or 1, len(page_numbers)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending: dict[Future[ScannedPage], int] = {}
        for page_no, image in _render(path, page_numbers):
            pending[pool.submit(read_scanned_page, image, page_no)] = page_no
            if len(pending) >= 2 * workers:
                done, _ = wait(pending, return_when=FIRST_COMPLETED)
                for future in done:
                    results[pending.pop(future)] = future.result()
        for future, page_no in pending.items():
            results[page_no] = future.result()
    return results


def read_scanned_page(image: Image.Image, page_no: int) -> ScannedPage:
    page = _prepare(image)
    if page is None:
        return ScannedPage([], [], [f"page {page_no}: blank page"])
    scans = [_scan(binary) for binary in page.binaries]
    found = [(scan, _find_header(scan)) for scan in scans]
    found.sort(key=lambda item: -_mapped_count(item[1]))  # stable: the first read wins a tie
    (primary, header), (secondary, _) = found[0], found[1]
    if header is None:
        texts = _texts(primary.lines, primary.height, page_no)
        return ScannedPage([], texts, [f"page {page_no}: no attendance table found"])

    header_index, cells = header
    cells = _second_opinion(cells, primary.lines[header_index], secondary)
    columns = _columns(cells, primary.height, page.frame)
    table, end = _table_lines(primary.lines, header_index, primary.height)
    warnings: list[str] = []
    rows = _rows(page, page_no, columns, table, primary, secondary, primary.lines[header_index].cy, warnings)
    if not rows:
        warnings.append(f"page {page_no}: attendance table header found but no rows could be read")
    return ScannedPage(rows, _texts(primary.lines[end:], primary.height, page_no), warnings)


# --- rendering and image clean-up -------------------------------------------------------------


def _check_tesseract() -> None:
    try:
        pytesseract.get_tesseract_version()
    except (pytesseract.TesseractNotFoundError, pytesseract.TesseractError, OSError) as exc:
        raise OcrUnavailableError(
            "This PDF has pages without a text layer, and the Tesseract OCR executable could not be run "
            f"(set TESSERACT_CMD): {exc}"
        ) from exc


def _render(path: Path, page_numbers: Sequence[int]) -> Iterator[tuple[int, Image.Image]]:
    document = pdfium.PdfDocument(str(path))
    try:
        for page_no in page_numbers:
            page = document[page_no - 1]
            try:
                bitmap = page.render(scale=DPI / 72, grayscale=True)
                image = bitmap.to_pil().copy()  # to_pil() shares the bitmap's memory
                bitmap.close()
            finally:
                page.close()
            yield page_no, image
    finally:
        document.close()


def _prepare(image: Image.Image) -> _Page | None:
    gray = image.convert("L")
    levels = _levels(gray)
    if levels is None:
        return None
    paper, ink = levels
    rule_cut = ink + (paper - ink) * 0.4
    angle = _skew(gray, rule_cut)
    if abs(angle) >= 0.05:
        gray = gray.rotate(angle, resample=Image.Resampling.BICUBIC, fillcolor=255)
    rules, frame = _ruling_lines(gray, rule_cut)
    gray.paste(255, mask=rules)
    if frame is not None:
        _clear_beside(gray, frame)
    cuts = [paper - (paper - ink) * factor for factor in CUTS]
    binaries = [gray.point(_binarise(cut)) for cut in cuts]
    black = gray.point(_lut(lambda v: 255 if v < cuts[0] else 0))
    grey = gray.point(_lut(lambda v: 255 if cuts[0] <= v < paper - PAPER_MARGIN else 0))
    far_grey = ImageChops.subtract(grey, _dilate(black, 3))
    return _Page(gray, binaries, black, far_grey, frame)


def _levels(gray: Image.Image) -> tuple[int, int] | None:
    """(paper, ink) grey levels: the histogram peak, and the median of the clearly dark pixels."""
    histogram = gray.histogram()
    paper = max(range(256), key=histogram.__getitem__)
    dark = histogram[: paper // 2]
    total = sum(dark)
    if total < MIN_INK_PIXELS:
        return None
    seen = 0
    for level, count in enumerate(dark):
        seen += count
        if seen * 2 >= total:
            return paper, level
    return paper, 0


def _skew(gray: Image.Image, cut: float) -> float:
    """The rotation (degrees) that makes text lines and ruling lines horizontal: the angle whose row
    profile is sharpest, searched coarse then fine on a reduced image."""
    small = gray.reduce(4).point(_lut(lambda v: 255 if v < cut else 0))
    best, best_score = 0.0, -1
    for step, span in ((0.25, MAX_SKEW_DEGREES), (0.05, 0.25)):
        centre, count = best, round(span / step)
        for k in range(-count, count + 1):
            angle = centre + k * step
            profile = small.rotate(angle, resample=Image.Resampling.BILINEAR).resize(
                (1, small.height), Image.Resampling.BOX
            )
            values = profile.tobytes()
            score = sum((a - b) ** 2 for a, b in itertools.pairwise(values))
            if score > best_score:
                best, best_score = angle, score
    return best


def _ruling_lines(gray: Image.Image, cut: float) -> tuple[Image.Image, _Box | None]:
    """A mask of the table's ruling lines, and the extent of its vertical lines."""
    ink = gray.point(_lut(lambda v: 255 if v < cut else 0))
    horizontal = _runs(ink, (RULE_RADIUS, 0))
    vertical = _runs(ink, (0, RULE_RADIUS))
    return _dilate(ImageChops.lighter(horizontal, vertical), 3), vertical.getbbox()


def _runs(ink: Image.Image, radius: tuple[int, int]) -> Image.Image:
    """Pixels on unbroken runs of ink along one axis (text strokes are far shorter than the window)."""
    core = ink.filter(ImageFilter.BoxBlur(radius)).point(_lut(lambda v: 255 if v >= 250 else 0))
    return core.filter(ImageFilter.BoxBlur(radius)).point(_lut(lambda v: 255 if v else 0))


def _dilate(mask: Image.Image, radius: int) -> Image.Image:
    return mask.filter(ImageFilter.BoxBlur(radius)).point(_lut(lambda v: 255 if v else 0))


def _clear_beside(gray: Image.Image, frame: _Box) -> None:
    """Blank the margins just outside the table frame, so specks there cannot join a row's first or last
    word."""
    left, top, right, bottom = frame
    band = DPI // 2
    gray.paste(255, (max(0, left - band), top, left, bottom))
    gray.paste(255, (right, top, min(gray.width, right + band), bottom))


def _lut(function: Callable[[int], int]) -> list[int]:
    return [function(value) for value in range(256)]


def _binarise(cut: float) -> list[int]:
    return [0 if value < cut else 255 for value in range(256)]


# --- words, lines and the header ------------------------------------------------------------------


def _scan(binary: Image.Image) -> _Scan:
    words = _words(binary, _TESSERACT_CONFIG)
    heights = [word.bottom - word.top for word in words if not word.is_noise]
    height = float(statistics.median(heights)) if heights else DPI / 10
    return _Scan(_group_lines(words, height), height)


def _words(image: Image.Image, config: str) -> list[_Word]:
    try:
        data = pytesseract.image_to_data(
            image, lang="eng", config=config, output_type=pytesseract.Output.DICT, timeout=TESSERACT_TIMEOUT
        )
    except pytesseract.TesseractError as exc:
        raise OcrUnavailableError(f"Tesseract failed: {exc}") from exc
    words: list[_Word] = []
    for index, raw in enumerate(data["text"]):
        text, conf = str(raw).strip(), float(data["conf"][index])
        if text and conf >= 0:
            left, top = int(data["left"][index]), int(data["top"][index])
            right, bottom = left + int(data["width"][index]), top + int(data["height"][index])
            words.append(_Word(text, conf / 100, left, top, right, bottom))
    return words


def _group_lines(words: list[_Word], height: float) -> list[_Line]:
    """Words whose vertical centres lie within 0.6 text heights of each other form a line (the page has
    been straightened, so rows are level)."""
    lines: list[_Line] = []
    members: list[list[_Word]] = []
    for word in sorted(words, key=lambda w: w.cy):
        if members and abs(word.cy - lines[-1].cy) <= height * 0.6:
            members[-1].append(word)
            lines[-1].cy = statistics.median(w.cy for w in members[-1])
        else:
            members.append([word])
            lines.append(_Line([], word.cy))
    for line, group in zip(lines, members, strict=True):
        line.words = sorted(group, key=lambda w: w.left)
    return lines


def _find_header(scan: _Scan) -> tuple[int, list[_HeaderCell]] | None:
    for index, line in enumerate(scan.lines):
        cells = _header_cells(line, scan.height)
        if len({cell.name for cell in cells if cell.name}) >= MIN_HEADER_FIELDS:
            return index, cells
    return None


def _mapped_count(header: tuple[int, list[_HeaderCell]] | None) -> int:
    return len({cell.name for cell in header[1] if cell.name}) if header else 0


def _header_cells(line: _Line, height: float) -> list[_HeaderCell]:
    """Header words grouped into cells by the gaps between them. A cell may span several words ("Emp ID",
    "Status code", "Date (DD/MM/YYYY)"); a group holding several field names is split between them."""
    words = [word for word in line.words if not word.is_noise]
    groups: list[list[_Word]] = []
    for word in words:
        if groups and word.left - groups[-1][-1].right <= height:
            groups[-1].append(word)
        else:
            groups.append([word])
    cells: list[_HeaderCell] = []
    for group in groups:
        spans = _field_spans(group)
        if len({name for name, _ in spans}) <= 1:
            cells.append(_HeaderCell(spans[0][0] if spans else None, tuple(group)))
            continue
        for k, (name, start) in enumerate(spans):
            begin = 0 if k == 0 else start
            end = spans[k + 1][1] if k + 1 < len(spans) else len(group)
            cells.append(_HeaderCell(name, tuple(group[begin:end])))
    return cells


def _field_spans(group: list[_Word]) -> list[tuple[str, int]]:
    """(field, first word index) for each run of words naming a field, longest match first."""
    spans: list[tuple[str, int]] = []
    start = 0
    while start < len(group):
        for end in range(min(len(group), start + 4), start, -1):
            name = header_field(" ".join(word.text for word in group[start:end]))
            if name:
                spans.append((name, start))
                start = end
                break
        else:
            start += 1
    return spans


def _fold(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower().translate(_CONFUSABLE)).strip()


_FOLDED = {_fold(synonym): name for name, synonyms in HEADER_SYNONYMS.items() for synonym in synonyms}


def header_field(text: str) -> str | None:
    """canonical_field, tolerant of OCR slips in a column header ("ln", "Emp 1D", "(OD/MM/YYYY)")."""
    if name := canonical_field(text):
        return name
    folded = _fold(text)
    if not folded:
        return None
    if folded in _FOLDED:
        return _FOLDED[folded]
    close = difflib.get_close_matches(folded, list(_FOLDED), n=1, cutoff=0.8)
    return _FOLDED[close[0]] if close else None


def _second_opinion(cells: list[_HeaderCell], line: _Line, other: _Scan) -> list[_HeaderCell]:
    """Name unrecognised header cells from the other read of the same header line."""
    twin = _nearest_line(other.lines, line.cy, other.height)
    if twin is None:
        return cells
    used = {cell.name for cell in cells if cell.name}
    named: list[_HeaderCell] = []
    for cell in cells:
        if cell.name is None:
            words = [w for w in twin.words if cell.left - 2 <= w.cx <= cell.right + 2 and not w.is_noise]
            name = header_field(" ".join(word.text for word in words)) if words else None
            if name and name not in used:
                used.add(name)
                cell = _HeaderCell(name, cell.words)
        named.append(cell)
    return named


def _nearest_line(lines: list[_Line], cy: float, height: float) -> _Line | None:
    near = [line for line in lines if abs(line.cy - cy) <= height * 0.5]
    return min(near, key=lambda line: abs(line.cy - cy)) if near else None


def _columns(cells: list[_HeaderCell], height: float, frame: _Box | None) -> list[_Column]:
    """Column boundaries from the header: values are aligned under their header, so a column starts just
    before its header text and ends where the next one starts. The outer edges are the table frame."""
    margin = height * 0.5
    inside = frame is not None and frame[0] <= cells[0].left and cells[-1].right <= frame[2]
    first = float(frame[0]) if frame is not None and inside else -math.inf
    last = float(frame[2]) if frame is not None and inside else math.inf
    starts = [first, *(cell.left - margin for cell in cells[1:])]
    ends = [*starts[1:], last]
    columns: list[_Column] = []
    for cell, start, end in zip(cells, starts, ends, strict=True):
        repeated = cell.name in {column.name for column in columns}
        columns.append(_Column(None if repeated else cell.name, start, end))  # the first wins
    return columns


def _table_lines(lines: list[_Line], header_index: int, height: float) -> tuple[list[_Line], int]:
    """The lines of the table body, and the index of the first line after the table: a caption such as
    "Note:" ends it, and so does a gap much wider than the row spacing."""
    table: list[_Line] = []
    previous = lines[header_index].cy
    for index in range(header_index + 1, len(lines)):
        line = lines[index]
        pitch = _pitch(table) or height * 3
        if _CAPTION.match(line.text) or line.cy - previous > max(3 * pitch, 4 * height):
            return table, index
        table.append(line)
        previous = line.cy
    return table, len(lines)


def _pitch(lines: list[_Line]) -> float | None:
    gaps = [b.cy - a.cy for a, b in itertools.pairwise(lines)]
    return float(statistics.median(gaps)) if gaps else None


# --- rows and confidence ----------------------------------------------------------------------------


def _rows(
    page: _Page,
    page_no: int,
    columns: list[_Column],
    table: list[_Line],
    primary: _Scan,
    secondary: _Scan,
    header_cy: float,
    warnings: list[str],
) -> list[ExtractedRow]:
    height = primary.height
    records: list[tuple[_Line, dict[str, str | None], dict[str, float]]] = []
    for line in table:
        twin = _nearest_line(secondary.lines, line.cy, height)
        reads = {
            column.name: (_cell(line, column, height), _cell(twin, column, height) if twin else _BLANK)
            for column in columns
            if column.name
        }
        merged = {name: _combine(*pair) for name, pair in reads.items()}
        if not is_record({name: value for name, (value, _) in merged.items()}):
            continue  # a caption, a footer or noise between the rows
        values: dict[str, str | None] = {}
        confidence: dict[str, float] = {}
        for column in columns:
            if column.name is None:
                continue
            value, conf = merged[column.name]
            if any(read.split for read in reads[column.name]):
                warnings.append(f"page {page_no}: a column boundary near '{value}' may have been missed")
                conf = min(conf, REVIEW_CAP)
            value, conf = _check_ink(page, column, line.cy, height, value, conf)
            if value is not None and not _is_plausible(column.name, value):
                conf = min(conf, REVIEW_CAP)
            values[column.name] = value
            confidence[column.name] = round(conf, 3)
        records.append((line, values, confidence))
    return _numbered(records, page_no, header_cy, warnings)


def _numbered(
    records: list[tuple[_Line, dict[str, str | None], dict[str, float]]],
    page_no: int,
    header_cy: float,
    warnings: list[str],
) -> list[ExtractedRow]:
    """Number the rows 1-based down the page. A gap of two or more row spacings means rows that could
    not be read at all: they keep their numbers, so later rows still point at the right place."""
    pitch = _pitch([line for line, _, _ in records])
    rows: list[ExtractedRow] = []
    row_no, previous = 0, header_cy
    for line, values, confidence in records:
        step = max(1, math.floor((line.cy - previous) / pitch + 0.35)) if pitch else 1
        if step > 1:
            warnings.append(f"page {page_no}: {step - 1} unreadable row(s) before row {row_no + step}")
        row_no += step
        previous = line.cy
        locator = Locator("page_row", page=page_no, row=row_no)
        rows.append(ExtractedRow(values, locator, "ocr", confidence))
    return rows


def _cell(line: _Line, column: _Column, height: float) -> _Cell:
    words = [word for word in line.words if column.start <= word.cx < column.end]
    real = [word for word in words if not word.is_noise]
    conf = min((word.conf for word in words), default=1.0)
    split = any(b.left - a.right > 2.5 * height for a, b in itertools.pairwise(real))
    return _Cell(" ".join(word.text for word in real) or None, conf, split)


def _combine(first: _Cell, second: _Cell) -> tuple[str | None, float]:
    """Merge the two reads of a cell. When they agree, the clearer read speaks for both (a blank cell keeps
    the lower score, so noise in either read counts). When they differ, the value is doubtful: take the
    read that found text with the higher score, capped for review."""
    if first.text == second.text:
        return first.text, max(first.conf, second.conf) if first.text else min(first.conf, second.conf)
    reads = [cell for cell in (first, second) if cell.text]
    best = max(reads, key=lambda cell: cell.conf)
    return best.text, min(max(first.conf, second.conf), REVIEW_CAP)


def _check_ink(
    page: _Page, column: _Column, cy: float, height: float, value: str | None, conf: float
) -> tuple[str | None, float]:
    """Cap cells holding smudged or faint ink. For a cell without text, 1.0 only if it is blank paper;
    otherwise try to read the faint ink on its own."""
    box = _cell_box(page, column, cy, height)
    if box is None:
        return value, conf
    limit = 0.05 * height * height
    degraded = _count(page.far_grey, box) >= limit
    if value is not None:
        return value, min(conf, REVIEW_CAP) if degraded else conf
    if not degraded and _count(page.black, box) < limit:
        return None, conf
    recovered = _read_faint(page.gray, box)
    return (recovered.text, min(recovered.conf, REVIEW_CAP)) if recovered.text else (None, 0.0)


def _cell_box(page: _Page, column: _Column, cy: float, height: float) -> _Box | None:
    """The inside of a cell, kept clear of its borders and of the neighbouring columns."""
    start = column.start if math.isfinite(column.start) else column.end - 12 * height
    end = column.end if math.isfinite(column.end) else start + 12 * height
    left, right = max(0, int(start + 0.3 * height)), min(page.gray.width, int(end - 0.6 * height))
    top, bottom = max(0, int(cy - 0.8 * height)), min(page.gray.height, int(cy + 0.8 * height))
    return (left, top, right, bottom) if right > left and bottom > top else None


def _count(mask: Image.Image, box: _Box) -> int:
    return mask.crop(box).histogram()[255]


def _read_faint(gray: Image.Image, box: _Box) -> _Cell:
    """Read a cell whose ink is too light for the page-level pass: stretch its contrast and OCR it as a
    single line."""
    crop = ImageOps.autocontrast(gray.crop(box), cutoff=1)
    binary = crop.point(_binarise(128))
    words = _words(ImageOps.expand(binary, border=40, fill=255), _LINE_CONFIG)
    real = [word for word in words if not word.is_noise]
    conf = min((word.conf for word in real), default=0.0)
    return _Cell(" ".join(word.text for word in real) or None, conf, split=False)


def _is_plausible(name: str, value: str) -> bool:
    pattern = _PLAUSIBLE.get(name)
    return pattern is None or pattern.fullmatch(value) is not None


# --- narrative text ---------------------------------------------------------------------------------


def _texts(lines: list[_Line], height: float, page_no: int) -> list[ExtractedText]:
    """Readable lines as paragraphs (a gap under 0.8 text heights continues a paragraph), without page
    numbers, "Verified by" lines and noise."""
    paragraphs: list[list[str]] = []
    previous: float | None = None
    for line in lines:
        words = [word for word in line.words if not word.is_noise or word.conf >= 0.6]
        text = " ".join(word.text for word in words)
        readable = sum(len(word.text) for word in words if word.conf >= 0.5 and not word.is_noise) >= 2
        if not readable or _SKIPPED_LINE.search(text):
            previous = None
            continue
        if previous is not None and line.cy - previous <= 1.8 * height and paragraphs:
            paragraphs[-1].append(text)
        else:
            paragraphs.append([text])
        previous = line.cy
    return [ExtractedText(" ".join(parts), Locator("page_text", page=page_no)) for parts in paragraphs]
