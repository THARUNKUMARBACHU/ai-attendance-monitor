"""Write the API's OpenAPI specification to docs/openapi.json.

Builds the app offline with throwaway settings (no .env, no servers, no model calls), with the demo
sign-in enabled so its endpoints are documented too.

Usage:
    uv run python scripts/export_openapi.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from attendance_ai.core.config import PROJECT_ROOT, Settings
from attendance_ai.main import create_app

TARGET = PROJECT_ROOT / "docs" / "openapi.json"


def main() -> int:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        app_env="dev",
        jwt_secret="openapi-export-only-" + "x" * 32,
        auth_dev_login_enabled=True,
        qdrant_url=None,
        openrouter_api_key=None,
        openai_api_key=None,
    )
    spec = create_app(settings).openapi()
    spec["info"]["description"] = (
        "Multi-tenant attendance intelligence: ingest attendance evidence, ask grounded questions with "
        "citations and confidence, export results, and improve answers through reviewer feedback. "
        "Every endpoint except health and the demo sign-in needs a bearer token (POST "
        "/api/v1/auth/dev-token in development). See README.md for examples."
    )
    Path(TARGET).parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    paths = sorted(spec.get("paths", {}))
    print(f"Wrote {TARGET.relative_to(PROJECT_ROOT)} ({len(paths)} paths):")
    for path in paths:
        methods = ", ".join(method.upper() for method in spec["paths"][path])
        print(f"  {methods:10s} {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
