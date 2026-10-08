"""The serverless (Vercel) entry's platform settings: defaults that the host's own settings override, and the
bundled Tesseract wired in when it is there."""

import os
from pathlib import Path

import pytest

from attendance_ai import serverless
from attendance_ai.serverless import PLATFORM_DEFAULTS, prepare_environment


def _fake_tesseract(root: Path) -> Path:
    binary = root / "bin" / "tesseract"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"#!/bin/sh\n")
    return binary


def test_fills_in_platform_defaults_but_never_overrides_the_host(tmp_path: Path) -> None:
    environ = {"UPLOAD_MAX_MB": "2"}
    prepare_environment(environ, tesseract_dir=tmp_path / "absent")
    assert environ == {**PLATFORM_DEFAULTS, "UPLOAD_MAX_MB": "2"}
    assert environ["INGESTION_MODE"] == "inline"
    assert "TESSERACT_CMD" not in environ  # no bundled Tesseract: OCR uses whatever is configured


def test_points_the_app_at_the_bundled_tesseract(tmp_path: Path) -> None:
    binary = _fake_tesseract(tmp_path / "tess")
    environ = {"LD_LIBRARY_PATH": "/opt/lib"}
    prepare_environment(environ, tesseract_dir=tmp_path / "tess", scratch_dir=tmp_path)
    assert environ["TESSERACT_CMD"] == str(binary)
    assert environ["LD_LIBRARY_PATH"] == os.pathsep.join([str(tmp_path / "tess" / "lib"), "/opt/lib"])
    assert environ["TESSDATA_PREFIX"] == str(tmp_path / "tess" / "tesseract" / "share" / "tessdata")


def test_uses_an_executable_copy_when_the_bundle_lost_the_executable_bit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_tesseract(tmp_path / "tess")
    monkeypatch.setattr(serverless.os, "access", lambda *_: False)
    environ: dict[str, str] = {}
    prepare_environment(environ, tesseract_dir=tmp_path / "tess", scratch_dir=tmp_path)
    copy = tmp_path / "tesseract"
    assert environ["TESSERACT_CMD"] == str(copy)
    assert copy.read_bytes() == b"#!/bin/sh\n"


def test_an_explicit_tesseract_path_wins(tmp_path: Path) -> None:
    _fake_tesseract(tmp_path / "tess")
    environ = {"TESSERACT_CMD": "/usr/bin/tesseract"}
    prepare_environment(environ, tesseract_dir=tmp_path / "tess", scratch_dir=tmp_path)
    assert environ["TESSERACT_CMD"] == "/usr/bin/tesseract"
