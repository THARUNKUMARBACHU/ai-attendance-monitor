"""Inline ingestion: the upload request runs its own job and retries temporary failures itself."""

import pytest

from attendance_ai.ingestion.inline import InlineJobQueue
from attendance_ai.ingestion.pipeline import RetryLaterError
from attendance_ai.ingestion.queue import MAX_ATTEMPTS, JobMessage

MESSAGE = JobMessage("job-1", "acme", "hrms", "attendance")


class ScriptedHandler:
    """Fails with a retryable error the given number of times, then succeeds."""

    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.calls: list[tuple[str, str, str, str]] = []

    def __call__(self, job_id: str, tenant_id: str, product_id: str, module: str) -> None:
        self.calls.append((job_id, tenant_id, product_id, module))
        if len(self.calls) <= self.failures:
            raise RetryLaterError("database unavailable")


def test_runs_the_job_once_when_it_succeeds() -> None:
    handler, pauses = ScriptedHandler(failures=0), list[float]()
    InlineJobQueue(handler, sleep=pauses.append).enqueue(MESSAGE)
    assert handler.calls == [("job-1", "acme", "hrms", "attendance")]
    assert pauses == []


def test_retries_temporary_failures_with_growing_pauses() -> None:
    handler, pauses = ScriptedHandler(failures=2), list[float]()
    InlineJobQueue(handler, pause_seconds=1.5, sleep=pauses.append).enqueue(MESSAGE)
    assert len(handler.calls) == 3
    assert pauses == [1.5, 3.0]


def test_gives_up_after_the_attempt_limit() -> None:
    handler, pauses = ScriptedHandler(failures=MAX_ATTEMPTS), list[float]()
    with pytest.raises(RetryLaterError):
        InlineJobQueue(handler, sleep=pauses.append).enqueue(MESSAGE)
    assert len(handler.calls) == MAX_ATTEMPTS
    assert len(pauses) == MAX_ATTEMPTS - 1
