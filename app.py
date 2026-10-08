"""Entry point for the Vercel deployment. Vercel's Python runtime loads the module-level `app` from
app.py at the project root; the application itself lives in src/attendance_ai.

Locally and in Docker, run the app with uvicorn and the factory instead (see README.md).
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from attendance_ai.serverless import prepare_environment

prepare_environment(os.environ)

from attendance_ai.main import create_app  # noqa: E402

app = create_app()
