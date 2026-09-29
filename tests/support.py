"""Test doubles and helpers shared by unit and integration tests."""

import os
import shutil
from pathlib import Path

from attendance_ai.core.access import AccessContext
from attendance_ai.core.config import PROJECT_ROOT, Settings
from attendance_ai.governance.audit import AuditEvent

TEST_JWT_SECRET = "unit-test-signing-secret-" + "k" * 40
SEED_FILE = PROJECT_ROOT / "sample_data" / "seed" / "tenants.json"
__all__ = ["PROJECT_ROOT", "SEED_FILE", "TEST_JWT_SECRET"]


def find_tesseract() -> str | None:
    """The Tesseract executable for OCR tests: TESSERACT_CMD, the PATH, or a standard Windows install
    (per user or machine-wide). None when it is not installed, and OCR tests then skip."""
    candidates = [os.environ.get("TESSERACT_CMD"), shutil.which("tesseract")]
    if local := os.environ.get("LOCALAPPDATA"):
        candidates.append(str(Path(local) / "Programs" / "Tesseract-OCR" / "tesseract.exe"))
    if program_files := os.environ.get("PROGRAMFILES"):
        candidates.append(str(Path(program_files) / "Tesseract-OCR" / "tesseract.exe"))
    return next((path for path in candidates if path and Path(path).is_file()), None)


def make_settings(**overrides: object) -> Settings:
    """Settings for tests: never reads .env, and never enables a real LLM provider by accident."""
    values: dict[str, object] = {
        "app_env": "test",
        "jwt_secret": TEST_JWT_SECRET,
        "auth_dev_login_enabled": True,
        "qdrant_url": None,
        "openrouter_api_key": None,
        "openai_api_key": None,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


class RecordingAudit:
    def __init__(self) -> None:
        self.events: list[tuple[AuditEvent, AccessContext | None]] = []

    def record(
        self, event: AuditEvent, ctx: AccessContext | None = None, *, request_id: str | None = None
    ) -> None:
        self.events.append((event, ctx))

    def actions(self) -> list[str]:
        return [event.action for event, _ in self.events]


class FakeDatabase:
    def __init__(self, healthy: bool = True) -> None:
        self.healthy = healthy

    def ping(self) -> None:
        if not self.healthy:
            raise ConnectionError("database unavailable")

    def dispose(self) -> None:
        pass


class FakeRedis:
    def __init__(self, healthy: bool = True) -> None:
        self.healthy = healthy

    def ping(self) -> bool:
        if not self.healthy:
            raise ConnectionError("redis unavailable")
        return True

    def close(self) -> None:
        pass
