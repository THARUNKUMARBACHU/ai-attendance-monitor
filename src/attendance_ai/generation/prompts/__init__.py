"""Versioned prompt files. The version (the file name) is recorded with every answer and audit event."""

from functools import cache
from pathlib import Path

_DIR = Path(__file__).parent

# v2: approved reviewer examples (the feedback loop) may be added to the user message.
PLANNER = "planner.v2"
COMPOSER = "composer.v2"


@cache
def load(name: str) -> str:
    return (_DIR / f"{name}.md").read_text(encoding="utf-8").strip()
