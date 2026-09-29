"""Build the submission zip, after checking that no secret can leak into it.

The zip holds the source, tests, migrations, scripts, sample data, docs, the UI source and the Docker
files. It leaves out local secrets (.env, .env.docker), virtual environments, caches, runtime data (var/),
node_modules and build output, the confidential assignment brief (doc/) and scratch files.

Before writing anything it scans every included text file for:
- the actual secret values in the local .env and .env.docker (API keys, passwords, the password part of
  every URL), matched exactly and never printed;
- key-like patterns (OpenAI/OpenRouter keys, private keys, AWS keys);
- the host names of the private cloud services behind the local .env.
It refuses to build the zip if anything is found.

Usage:
    uv run python scripts/package_submission.py            # writes dist/attendance-intelligence.zip
    uv run python scripts/package_submission.py --check    # scan only
"""

from __future__ import annotations

import argparse
import re
import sys
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "dist" / "attendance-intelligence.zip"
PREFIX = "attendance-intelligence"

EXCLUDED_DIRS = {
    ".git",
    ".claude",
    ".idea",
    ".vscode",
    ".venv",
    "venv",
    "__pycache__",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    "node_modules",
    "var",
    "dist",
    "build",
}
EXCLUDED_TOP_LEVEL = {"doc", "test.py"}  # the confidential brief and a personal scratch file
EXCLUDED_NAMES = {".DS_Store", "Thumbs.db"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".log", ".tsbuildinfo"}
ALLOWED_ENV_FILES = {".env.example", ".env.docker.example"}
SECRET_ENV_FILES = (".env", ".env.docker")
TEXT_SUFFIXES = {
    ".py",
    ".md",
    ".json",
    ".jsonl",
    ".toml",
    ".ini",
    ".cfg",
    ".yml",
    ".yaml",
    ".txt",
    ".csv",
    ".ts",
    ".tsx",
    ".js",
    ".mjs",
    ".css",
    ".html",
    ".sql",
    ".example",
    ".lock",
    ".mako",
    "",
}
KEY_PATTERNS = {
    "OpenAI/OpenRouter-style API key": re.compile(r"\bsk-(?:or-v1-|proj-)?[A-Za-z0-9_-]{24,}"),
    "private key block": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
}
PLACEHOLDERS = {"", "<password>", "change-me", "changeme", "password", "secret"}


def included_files() -> list[Path]:
    files: list[Path] = []
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(ROOT)
        parts = relative.parts
        if parts[0] in EXCLUDED_TOP_LEVEL or any(part in EXCLUDED_DIRS for part in parts[:-1]):
            continue
        if path.name in EXCLUDED_NAMES or path.suffix in EXCLUDED_SUFFIXES:
            continue
        if path.name.startswith(".env") and path.name not in ALLOWED_ENV_FILES:
            continue
        if parts[:2] == ("frontend", "dist"):
            continue
        files.append(path)
    return files


def secret_values() -> tuple[list[str], set[str]]:
    """The secret values and private host names in the local env files (kept in memory only)."""
    values: set[str] = set()
    hosts: set[str] = set()
    for name in SECRET_ENV_FILES:
        path = ROOT / name
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if "=" not in line or line.lstrip().startswith("#"):
                continue
            key, _, value = line.partition("=")
            key, value = key.strip().upper(), value.strip().strip('"').strip("'")
            if "://" in value:
                parts = urlsplit(value)
                if parts.password:
                    values.add(parts.password)
                host = parts.hostname or ""
                if (
                    "." in host
                    and not host.endswith((".local", "localhost"))
                    and host not in ("postgres", "redis", "qdrant")
                ):
                    hosts.add(host)
            elif any(word in key for word in ("KEY", "SECRET", "PASSWORD", "TOKEN")):
                values.add(value)
    secrets = sorted(
        v for v in values if len(v) >= 8 and v.lower() not in PLACEHOLDERS and "change-me" not in v
    )
    return secrets, hosts


def scan(files: list[Path]) -> list[str]:
    secrets, hosts = secret_values()
    findings: list[str] = []
    for path in files:
        if path.suffix.lower() not in TEXT_SUFFIXES and path.stat().st_size > 5_000_000:
            continue
        try:
            text = path.read_bytes().decode("utf-8", errors="ignore")
        except OSError:
            continue
        relative = path.relative_to(ROOT).as_posix()
        for index, secret in enumerate(secrets, start=1):
            if secret in text:
                findings.append(f"{relative}: contains local secret value #{index} from .env")
        for label, pattern in KEY_PATTERNS.items():
            if pattern.search(text):
                findings.append(f"{relative}: looks like it contains a {label}")
        for host in hosts:
            if host in text:
                findings.append(f"{relative}: names the private host {host}")
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the submission zip after a secret scan.")
    parser.add_argument("--check", action="store_true", help="scan only; do not write the zip")
    args = parser.parse_args()

    files = included_files()
    findings = scan(files)
    total = sum(path.stat().st_size for path in files)
    print(f"{len(files)} files, {total / 1_048_576:.1f} MB before compression")
    if findings:
        print("\nRefusing to package: possible secrets found (values not shown):")
        for finding in findings:
            print(f"  - {finding}")
        return 1
    print("Secret scan: clean (local .env values, key patterns and private hosts).")
    if args.check:
        return 0

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(OUTPUT, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in files:
            archive.write(path, f"{PREFIX}/{path.relative_to(ROOT).as_posix()}")
    top_level = sorted({path.relative_to(ROOT).parts[0] for path in files})
    print(f"Wrote {OUTPUT.relative_to(ROOT)} ({OUTPUT.stat().st_size / 1_048_576:.1f} MB)")
    print("Top level: " + ", ".join(top_level))
    return 0


if __name__ == "__main__":
    sys.exit(main())
