import io
import zipfile

import pytest

from attendance_ai.ingestion.file_checks import (
    FileRejectedError,
    FileTooLargeError,
    UnsupportedFileTypeError,
    inspect_upload,
    safe_filename,
)
from attendance_ai.ingestion.types import MEDIA_CSV, MEDIA_DOCX, MEDIA_PDF, MEDIA_XLSX

from ..support import PROJECT_ROOT

INPUTS = PROJECT_ROOT / "sample_data" / "tenants" / "acme" / "inputs"
LIMIT = 20 * 1024 * 1024


def _office_zip(content_type: str, extra: dict[str, bytes] | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", f'<Types><Override ContentType="{content_type}"/></Types>')
        for name, data in (extra or {}).items():
            archive.writestr(name, data)
    return buffer.getvalue()


@pytest.mark.parametrize(
    ("name", "media_type"),
    [
        ("acme_engineering_biometric_2026-08.csv", MEDIA_CSV),
        ("acme_sales_muster_2026-08.xlsx", MEDIA_XLSX),
        ("acme_operations_weekly_report_2026-08.docx", MEDIA_DOCX),
        ("acme_finance_attendance_2026-08.pdf", MEDIA_PDF),
        ("acme_hr_register_scanned_2026-08.pdf", MEDIA_PDF),
    ],
)
def test_sample_files_are_recognised_by_content(name: str, media_type: str) -> None:
    data = (INPUTS / name).read_bytes()
    assert inspect_upload(name, data, max_bytes=LIMIT).media_type == media_type


def test_content_must_match_the_extension() -> None:
    pdf = (INPUTS / "acme_finance_attendance_2026-08.pdf").read_bytes()
    with pytest.raises(FileRejectedError, match="content is PDF"):
        inspect_upload("report.csv", pdf, max_bytes=LIMIT)


def test_rejects_empty_oversized_and_unknown_files() -> None:
    with pytest.raises(FileRejectedError, match="empty"):
        inspect_upload("a.csv", b"", max_bytes=LIMIT)
    with pytest.raises(FileTooLargeError):
        inspect_upload("a.csv", b"a,b\n" * 100, max_bytes=10)
    with pytest.raises(UnsupportedFileTypeError):
        inspect_upload("a.exe", b"MZ\x90\x00" + b"\x00" * 64, max_bytes=LIMIT)
    with pytest.raises(UnsupportedFileTypeError):
        inspect_upload("notes.txt", b"just some words without separators", max_bytes=LIMIT)


def test_rejects_legacy_or_encrypted_office_files() -> None:
    with pytest.raises(UnsupportedFileTypeError, match="password-protected"):
        inspect_upload("old.xls", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64, max_bytes=LIMIT)


def test_rejects_macro_enabled_workbooks() -> None:
    data = _office_zip(
        "application/vnd.ms-excel.sheet.macroEnabled.main+xml", {"xl/vbaProject.bin": b"\x00" * 16}
    )
    with pytest.raises(UnsupportedFileTypeError, match="macros"):
        inspect_upload("book.xlsx", data, max_bytes=LIMIT)


def test_rejects_zip_bombs() -> None:
    bomb = _office_zip(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
        {"xl/worksheets/sheet1.xml": b"0" * (5 * 1024 * 1024)},
    )
    with pytest.raises(FileRejectedError, match="unsafe size"):
        inspect_upload("book.xlsx", bomb, max_bytes=LIMIT)


def test_safe_filename_strips_paths_and_odd_characters() -> None:
    assert safe_filename("..\\..\\evil/<script>.csv", ".csv") == "script.csv"
    assert safe_filename("Monthly report (Aug).CSV", ".csv") == "Monthly report (Aug).csv"
    assert safe_filename("", ".pdf") == "upload.pdf"
