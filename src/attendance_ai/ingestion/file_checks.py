"""Upload checks: the real file type (from content, not the name), size, and unsafe archives."""

import io
import re
import zipfile
from dataclasses import dataclass
from pathlib import PurePath

from attendance_ai.core.errors import AppError
from attendance_ai.ingestion.types import MEDIA_CSV, MEDIA_DOCX, MEDIA_PDF, MEDIA_XLSX

EXTENSIONS = {MEDIA_CSV: ".csv", MEDIA_XLSX: ".xlsx", MEDIA_DOCX: ".docx", MEDIA_PDF: ".pdf"}
_LABELS = {MEDIA_CSV: "CSV", MEDIA_XLSX: "Excel (.xlsx)", MEDIA_DOCX: "Word (.docx)", MEDIA_PDF: "PDF"}

# XLSX and DOCX are zip archives; refuse ones that expand to something unreasonable ("zip bombs").
MAX_UNCOMPRESSED_BYTES = 200 * 1024 * 1024
MAX_COMPRESSION_RATIO = 100
MAX_ARCHIVE_ENTRIES = 5000


class FileRejectedError(AppError):
    status_code = 400
    code = "file_rejected"
    title = "File rejected"


class FileTooLargeError(FileRejectedError):
    status_code = 413
    code = "file_too_large"
    title = "File too large"


class UnsupportedFileTypeError(FileRejectedError):
    status_code = 415
    code = "unsupported_file_type"
    title = "Unsupported file type"


@dataclass(frozen=True, slots=True)
class InspectedFile:
    media_type: str
    extension: str
    size_bytes: int


def inspect_upload(filename: str, data: bytes, *, max_bytes: int) -> InspectedFile:
    if not data:
        raise FileRejectedError("The file is empty.")
    if len(data) > max_bytes:
        raise FileTooLargeError(f"The file is larger than the {max_bytes // (1024 * 1024)} MB limit.")
    if data.startswith(b"%PDF-"):
        media_type = MEDIA_PDF
    elif data.startswith(b"PK\x03\x04"):
        media_type = _inspect_office_archive(data)
    elif data.startswith(b"\xd0\xcf\x11\xe0"):
        raise UnsupportedFileTypeError(
            "Legacy or password-protected Office files are not supported. Save it as .xlsx or .docx "
            "without a password."
        )
    else:
        media_type = _inspect_text(data)
    extension = PurePath(filename).suffix.lower()
    expected = EXTENSIONS[media_type]
    if extension and extension != expected:
        raise FileRejectedError(
            f"The file's content is {_LABELS[media_type]}, but its name ends in '{extension}'."
        )
    return InspectedFile(media_type=media_type, extension=expected, size_bytes=len(data))


def safe_filename(filename: str, extension: str) -> str:
    """A display- and storage-safe version of the uploaded name (no paths, no odd characters)."""
    name = PurePath(filename.replace("\\", "/")).name
    stem = re.sub(r"[^A-Za-z0-9._() -]+", "_", PurePath(name).stem).strip(" ._") or "upload"
    return f"{stem[:150]}{extension}"


def _inspect_office_archive(data: bytes) -> str:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise FileRejectedError("The file is damaged and cannot be opened.") from exc
    entries = archive.infolist()
    if len(entries) > MAX_ARCHIVE_ENTRIES:
        raise FileRejectedError("The file contains too many parts to be processed safely.")
    expanded = sum(entry.file_size for entry in entries)
    compressed = max(1, sum(entry.compress_size for entry in entries))
    if expanded > MAX_UNCOMPRESSED_BYTES or expanded / compressed > MAX_COMPRESSION_RATIO:
        raise FileRejectedError("The file expands to an unsafe size and was not processed.")
    if any(entry.flag_bits & 0x1 for entry in entries):
        raise UnsupportedFileTypeError("Encrypted files are not supported.")
    names = {entry.filename for entry in entries}
    if "[Content_Types].xml" not in names:
        raise UnsupportedFileTypeError("Only .xlsx and .docx Office files are supported.")
    content_types = archive.read("[Content_Types].xml").decode("utf-8", errors="replace")
    if "vbaProject" in content_types or any(name.lower().endswith("vbaproject.bin") for name in names):
        raise UnsupportedFileTypeError("Files containing macros are not accepted.")
    if "spreadsheetml.sheet.main+xml" in content_types:
        return MEDIA_XLSX
    if "wordprocessingml.document.main+xml" in content_types:
        return MEDIA_DOCX
    raise UnsupportedFileTypeError("Only .xlsx and .docx Office files are supported.")


def _inspect_text(data: bytes) -> str:
    if b"\x00" in data[:8192]:
        raise UnsupportedFileTypeError("Supported types are CSV, XLSX, DOCX and PDF.")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise UnsupportedFileTypeError("Text files must be UTF-8 encoded CSV.") from exc
    first_line = next((line for line in text.splitlines() if line.strip()), "")
    if not any(separator in first_line for separator in (",", ";", "\t")):
        raise UnsupportedFileTypeError("Supported types are CSV, XLSX, DOCX and PDF.")
    return MEDIA_CSV
