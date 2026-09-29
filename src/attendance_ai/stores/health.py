"""Dependency checks behind the readiness endpoint."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass
from typing import Literal

import httpx

from attendance_ai.core.config import Settings

CheckStatus = Literal["up", "down", "configured", "not_configured"]


@dataclass(frozen=True, slots=True)
class CheckResult:
    status: CheckStatus
    latency_ms: int | None = None
    detail: str | None = None


def run_check(check: Callable[[], object]) -> CheckResult:
    """Run one check. Failures report only the error type: messages can name hosts or users."""
    started = time.perf_counter()
    try:
        check()
    except Exception as exc:  # any failure means the dependency is down
        return CheckResult("down", _elapsed_ms(started), type(exc).__name__)
    return CheckResult("up", _elapsed_ms(started))


def run_checks(checks: Mapping[str, Callable[[], object]], deadline_seconds: float) -> dict[str, CheckResult]:
    """Run checks in parallel and return within the deadline. A check still running then is reported
    as down, so a hung or slow-failing dependency cannot stall the readiness probe."""
    executor = ThreadPoolExecutor(max_workers=max(1, len(checks)), thread_name_prefix="health-check")
    futures = {name: executor.submit(run_check, check) for name, check in checks.items()}
    done, _ = wait(futures.values(), timeout=deadline_seconds)
    executor.shutdown(wait=False, cancel_futures=True)
    timed_out = CheckResult("down", round(deadline_seconds * 1000), "Timeout")
    return {name: future.result() if future in done else timed_out for name, future in futures.items()}


def ping_qdrant(settings: Settings) -> None:
    if settings.qdrant_url is None:
        raise ValueError("QDRANT_URL is not set")
    headers = {"api-key": settings.qdrant_api_key.get_secret_value()} if settings.qdrant_api_key else {}
    response = httpx.get(
        f"{settings.qdrant_url.rstrip('/')}/readyz",
        headers=headers,
        timeout=settings.health_check_timeout_seconds,
    )
    response.raise_for_status()


def _elapsed_ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)
