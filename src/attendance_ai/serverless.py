"""Settings for running as a serverless function (the Vercel deployment; see app.py at the project root).

A function host differs from a server in four ways that matter here: the code is read-only except /tmp,
there is no background worker, request bodies are capped at 4.5 MB, and many small instances share the
database's connection limit. `prepare_environment` adapts the app to that before it is created.
Environment variables set on the host always win over these defaults. Locally and in Docker none of this
runs: uvicorn builds the app with the factory (attendance_ai.main:create_app --factory).
"""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import MutableMapping
from pathlib import Path

from attendance_ai.core.config import PROJECT_ROOT

# Put there by the build step (scripts/vercel_build.py): a Tesseract build for Amazon Linux 2023.
TESSERACT_DIR = PROJECT_ROOT / "vendor" / "tesseract"
# The only writable folder on a function host (/tmp there).
WRITABLE_DIR = Path(tempfile.gettempdir())

PLATFORM_DEFAULTS: dict[str, str] = {
    "INGESTION_MODE": "inline",  # no background worker: the upload request processes its file
    "STORAGE_DIR": str(WRITABLE_DIR / "storage"),  # uploads, kept only while they are processed
    "UPLOAD_MAX_MB": "4",  # the host rejects request bodies over 4.5 MB
    "DB_POOL_SIZE": "2",  # instances share the database's connection limit
    "HF_HUB_OFFLINE": "1",  # the models are bundled at build time and never downloaded at run time
}


def prepare_environment(
    environ: MutableMapping[str, str],
    *,
    tesseract_dir: Path = TESSERACT_DIR,
    scratch_dir: Path = WRITABLE_DIR,
) -> None:
    """Fill in the platform defaults, and point the app at the bundled Tesseract when it is there."""
    for key, value in PLATFORM_DEFAULTS.items():
        environ.setdefault(key, value)

    binary = tesseract_dir / "bin" / "tesseract"
    if not binary.is_file():
        return
    # Tesseract runs as a child process, which inherits these.
    environ["LD_LIBRARY_PATH"] = os.pathsep.join(
        part for part in (str(tesseract_dir / "lib"), environ.get("LD_LIBRARY_PATH", "")) if part
    )
    environ.setdefault("TESSDATA_PREFIX", str(tesseract_dir / "tesseract" / "share" / "tessdata"))
    if not os.access(binary, os.X_OK):
        # The deployed bundle may drop the executable bit; a copy in the writable folder can have it.
        copy = scratch_dir / "tesseract"
        if not copy.exists():
            shutil.copy2(binary, copy)
            copy.chmod(0o755)
        binary = copy
    environ.setdefault("TESSERACT_CMD", str(binary))
