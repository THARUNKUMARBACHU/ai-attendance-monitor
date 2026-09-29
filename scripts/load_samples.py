"""Load the sample attendance files through the running API, as each tenant's admin, and wait for them.

Usage:
    uv run python scripts/load_samples.py                     # the 7 input files + the prompt-injection memo
    uv run python scripts/load_samples.py --changed-file      # then re-upload v1 (duplicate) and upload v2

Needs the API (with AUTH_DEV_LOGIN_ENABLED=true) and the worker running.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any

import httpx

SAMPLES = Path(__file__).resolve().parents[1] / "sample_data" / "tenants"
TERMINAL = {"completed", "completed_with_errors", "failed", "duplicate"}

INPUTS: list[tuple[str, str, str | None]] = [
    ("acme.admin", "acme/inputs/acme_engineering_biometric_2026-08.csv", None),
    ("acme.admin", "acme/inputs/acme_sales_muster_2026-08.xlsx", None),
    ("acme.admin", "acme/inputs/acme_operations_weekly_report_2026-08.docx", "OPS"),
    ("acme.admin", "acme/inputs/acme_finance_attendance_2026-08.pdf", "FIN"),
    ("acme.admin", "acme/inputs/acme_hr_register_scanned_2026-08.pdf", "HR"),
    ("globex.admin", "globex/inputs/globex_engineering_biometric_2026-08.csv", None),
    ("globex.admin", "globex/inputs/globex_support_muster_2026-08.xlsx", None),
    ("acme.admin", "acme/scenarios/prompt_injection/acme_operations_memo_2026-08.docx", "OPS"),
]
CHANGED_FILE: list[tuple[str, str, str | None]] = [
    ("acme.admin", "acme/inputs/acme_engineering_biometric_2026-08.csv", None),
    ("acme.admin", "acme/scenarios/changed_file/acme_engineering_biometric_2026-08.csv", None),
]


def login(client: httpx.Client, user_id: str) -> dict[str, str]:
    response = client.post("/api/v1/auth/dev-token", json={"user_id": user_id})
    response.raise_for_status()
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def upload(client: httpx.Client, headers: dict[str, str], path: Path, entity: str | None) -> dict[str, Any]:
    data = {"entity_id": entity} if entity else {}
    with path.open("rb") as handle:
        response = client.post(
            "/api/v1/ingestions", headers=headers, files={"file": (path.name, handle)}, data=data
        )
    if response.status_code != 202:
        return {"status": f"rejected ({response.status_code})", "detail": response.json().get("detail")}
    return dict(response.json())


def wait(client: httpx.Client, headers: dict[str, str], job_id: str, timeout: float = 600) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while True:
        job = client.get(f"/api/v1/ingestions/{job_id}", headers=headers).json()
        if job["status"] in TERMINAL or time.monotonic() > deadline:
            return dict(job)
        time.sleep(2)


def run(base_url: str, plan: list[tuple[str, str, str | None]]) -> bool:
    ok = True
    with httpx.Client(base_url=base_url, timeout=120) as client:
        tokens: dict[str, dict[str, str]] = {}
        for user_id, relative, entity in plan:
            headers = tokens.setdefault(user_id, login(client, user_id))
            path = SAMPLES / relative
            receipt = upload(client, headers, path, entity)
            if "job_id" not in receipt:
                print(f"  {path.name:52s} {receipt['status']}: {receipt.get('detail')}")
                ok = False
                continue
            job = wait(client, headers, receipt["job_id"])
            counts = job.get("counts") or {}
            summary = ", ".join(
                f"{key.replace('records_', '')}={counts[key]}"
                for key in (
                    "rows_read",
                    "records_created",
                    "records_updated",
                    "records_unchanged",
                    "needs_review",
                    "rejected",
                    "chunks_indexed",
                    "chunks_quarantined",
                )
                if counts.get(key)
            )
            print(f"  {path.name:52s} v{receipt['version_no']} {job['status']:22s} {summary}")
            ok = ok and job["status"] in ("completed", "completed_with_errors", "duplicate")
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--changed-file", action="store_true", help="re-upload v1, then upload the changed v2"
    )
    args = parser.parse_args()
    plan = CHANGED_FILE if args.changed_file else INPUTS
    print(f"Uploading {len(plan)} files to {args.base_url}")
    return 0 if run(args.base_url, plan) else 1


if __name__ == "__main__":
    sys.exit(main())
