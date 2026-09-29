"""The answer pipeline end to end against PostgreSQL, with a scripted (mock) LLM: deterministic, free."""

from collections.abc import Callable
from datetime import date
from typing import Any

import pytest
from sqlalchemy import func, select

from attendance_ai.core.access import AccessContext
from attendance_ai.core.directory import Directory
from attendance_ai.generation.mock import MockProvider
from attendance_ai.generation.router import LLMRouter
from attendance_ai.governance.audit import AuditLog
from attendance_ai.orchestration.answer import AnswerService
from attendance_ai.orchestration.models import QueryRequest
from attendance_ai.retrieval.sql_guard import GuardedSql
from attendance_ai.retrieval.sql_runner import SqlExecutionError, SqlRunner
from attendance_ai.stores.db import Database
from attendance_ai.stores.models import QueryLogEntry

Ctx = Callable[[str], AccessContext]


class ScriptedLLM:
    """Plans and answers from queues, telling the planner and composer calls apart by their prompts."""

    def __init__(self, plans: list[dict[str, Any]], answers: list[dict[str, Any]] | None = None) -> None:
        self.plans = plans
        self.answers = answers or []
        self.planner_prompts: list[str] = []
        self.composer_prompts: list[str] = []

    def __call__(self, system: str, user: str) -> dict[str, Any]:
        if system.startswith("You plan"):
            self.planner_prompts.append(user)
            return self.plans.pop(0) if len(self.plans) > 1 else self.plans[0]
        self.composer_prompts.append(user)
        return self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]


def _plan(sql: str | None, mode: str = "structured") -> dict[str, Any]:
    return {"mode": mode, "sql": sql, "search_query": None, "rewritten_question": "q", "reason": "test"}


def _service(database: Database, directory: Directory, *providers: MockProvider) -> AnswerService:
    return AnswerService(
        directory=directory,
        database=database,
        router=LLMRouter(list(providers)),
        sql_runner=SqlRunner(database, row_cap=200, evidence_cap=2000),
        documents=None,
        cache=None,
        audit=AuditLog(database),
        max_tokens=500,
    )


PCT = (
    "SELECT ROUND(100.0 * SUM(attendance_weight) / NULLIF(COUNT(attendance_weight), 0), 2) "
    "AS attendance_pct, COUNT(attendance_weight) AS counted_days "
    "FROM v_attendance WHERE source_file = '{name}'"
)
FOUR_DAYS = [{"status": "PRESENT"}, {"status": "PRESENT"}, {"status": "HALF_DAY"}, {"status": "ABSENT"}]


def test_structured_answer_with_citations_confidence_and_log(
    database: Database, directory: Directory, ctx_for: Ctx, add_records: Any
) -> None:
    name = add_records("acme", FOUR_DAYS)
    llm = ScriptedLLM(
        [_plan(PCT.format(name=name))],
        [{"answer": "Attendance was 62.5% over 4 counted days [D1].", "citations": ["D1"]}],
    )
    ctx = ctx_for("acme.admin")
    response = _service(database, directory, MockProvider(llm)).answer(
        ctx, QueryRequest(question="What was attendance?")
    )
    assert response.outcome == "answered", response
    assert "62.5%" in (response.answer or "")
    assert response.citations[0].id == "D1" and response.citations[0].record_count == 4
    assert response.citations[0].source_file == name
    assert response.computation is not None and response.computation.result_preview == [
        {"attendance_pct": 62.5, "counted_days": 4}
    ]
    assert response.confidence is not None and response.confidence.band == "high"
    assert "RESULT TABLE" in llm.composer_prompts[0]
    with database.session_for(ctx) as session:
        logged = session.get(QueryLogEntry, ctx.request_id)
    assert logged is not None and logged.outcome == "answered" and logged.sql_text


def test_managers_only_ever_count_their_departments(
    database: Database, directory: Directory, ctx_for: Ctx, add_records: Any
) -> None:
    name = add_records(
        "acme", [{"entity_id": "ENG", "employee_id": "E002"}, {"entity_id": "SAL", "employee_id": "E007"}]
    )
    llm = ScriptedLLM(
        [_plan(f"SELECT COUNT(*) AS n FROM v_attendance WHERE source_file = '{name}'")],
        [{"answer": "There is 1 record [D1].", "citations": ["D1"]}],
    )
    response = _service(database, directory, MockProvider(llm)).answer(
        ctx_for("acme.eng.manager"), QueryRequest(question="How many records are there?")
    )
    assert response.computation is not None and response.computation.result_preview == [{"n": 1}]


def test_other_tenants_data_is_simply_not_there(
    database: Database, directory: Directory, ctx_for: Ctx, add_records: Any
) -> None:
    name = add_records("acme", FOUR_DAYS)
    llm = ScriptedLLM([_plan(PCT.format(name=name))], [{"answer": "unused", "citations": []}])
    response = _service(database, directory, MockProvider(llm)).answer(
        ctx_for("globex.admin"), QueryRequest(question="What was attendance in that file?")
    )
    assert (response.outcome, response.reason_code) == ("unavailable", "no_data")
    assert llm.composer_prompts == []  # nothing retrieved, so the composer is never called


def test_out_of_scope_questions_are_denied_before_any_model_call(
    database: Database, directory: Directory, ctx_for: Ctx
) -> None:
    provider = MockProvider(ScriptedLLM([_plan("SELECT 1")]))
    response = _service(database, directory, provider).answer(
        ctx_for("acme.eng.manager"), QueryRequest(question="What was the attendance percentage for Sales?")
    )
    assert (response.outcome, response.reason_code) == ("denied", "out_of_scope_entity")
    assert provider.calls == []


def test_fallback_model_then_controlled_failure(
    database: Database, directory: Directory, ctx_for: Ctx, add_records: Any
) -> None:
    name = add_records("acme", FOUR_DAYS)
    script = ScriptedLLM(
        [_plan(PCT.format(name=name))], [{"answer": "Attendance was 62.5% [D1].", "citations": ["D1"]}]
    )
    primary, fallback = MockProvider(script, model="primary"), MockProvider(script, model="fallback")
    service = _service(database, directory, primary, fallback)
    primary.failing = True
    answered = service.answer(ctx_for("acme.admin"), QueryRequest(question="What was attendance?"))
    assert answered.outcome == "answered"
    assert answered.confidence is not None and any("fallback" in r for r in answered.confidence.reasons)
    fallback.failing = True
    failed = service.answer(ctx_for("acme.admin"), QueryRequest(question="What was attendance?"))
    assert (failed.outcome, failed.reason_code) == ("unavailable", "provider_unavailable")


def test_ungrounded_drafts_are_repaired_or_replaced(
    database: Database, directory: Directory, ctx_for: Ctx, add_records: Any
) -> None:
    name = add_records("acme", FOUR_DAYS)
    wrong = {"answer": "Attendance was 100% [D1].", "citations": ["D1"]}
    right = {"answer": "Attendance was 62.5% [D1].", "citations": ["D1"]}
    repaired = _service(
        database, directory, MockProvider(ScriptedLLM([_plan(PCT.format(name=name))], [wrong, right]))
    ).answer(ctx_for("acme.admin"), QueryRequest(question="What was attendance?"))
    assert repaired.answer == "Attendance was 62.5% [D1]."
    assert repaired.confidence is not None and any("corrected" in r for r in repaired.confidence.reasons)

    replaced = _service(
        database, directory, MockProvider(ScriptedLLM([_plan(PCT.format(name=name))], [wrong, wrong]))
    ).answer(ctx_for("acme.admin"), QueryRequest(question="What was attendance?"))
    assert (replaced.answer or "").startswith("Result: attendance pct: 62.5")
    assert "100" not in (replaced.answer or "")


def test_unsafe_sql_is_rejected_and_repaired(
    database: Database, directory: Directory, ctx_for: Ctx, add_records: Any
) -> None:
    name = add_records("acme", FOUR_DAYS)
    llm = ScriptedLLM(
        [_plan("SELECT set_config('app.tenant_id', 'globex', true)"), _plan(PCT.format(name=name))],
        [{"answer": "Attendance was 62.5% [D1].", "citations": ["D1"]}],
    )
    response = _service(database, directory, MockProvider(llm)).answer(
        ctx_for("acme.admin"), QueryRequest(question="What was attendance?")
    )
    assert response.outcome == "answered"
    assert "set_config() is not allowed" in llm.planner_prompts[1]


def test_restricted_remarks_stay_masked_for_managers(
    database: Database, directory: Directory, ctx_for: Ctx, add_records: Any
) -> None:
    name = add_records(
        "acme",
        [
            {
                "employee_id": "E003",
                "status": "LEAVE",
                "remarks": "Sick leave - fever",
                "remarks_sensitivity": "restricted",
            }
        ],
    )
    llm = ScriptedLLM(
        [_plan(f"SELECT employee_id, status, remarks FROM v_attendance WHERE source_file = '{name}'")],
        [{"answer": "E003 was on leave; the reason is restricted [D1].", "citations": ["D1"]}],
    )
    response = _service(database, directory, MockProvider(llm)).answer(
        ctx_for("acme.eng.manager"), QueryRequest(question="Why was E003 on leave?")
    )
    assert response.computation is not None
    assert response.computation.result_preview[0]["remarks"] == "[restricted]"
    assert "fever" not in llm.composer_prompts[0]


def test_records_waiting_for_review_are_excluded_and_disclosed(
    database: Database, directory: Directory, ctx_for: Ctx, add_records: Any
) -> None:
    name = add_records(
        "acme",
        [
            {"employee_id": "E018", "attendance_date": date(2031, 3, 3)},
            {"employee_id": "E019", "attendance_date": date(2031, 3, 3), "review_status": "needs_review"},
        ],
    )
    llm = ScriptedLLM(
        [_plan(f"SELECT COUNT(*) AS n FROM v_attendance WHERE source_file = '{name}'")],
        [{"answer": "There is 1 record [D1].", "citations": ["D1"]}],
    )
    response = _service(database, directory, MockProvider(llm)).answer(
        ctx_for("acme.admin"), QueryRequest(question="How many records?")
    )
    assert response.computation is not None and response.computation.result_preview == [{"n": 1}]
    assert response.computation.excluded_pending_review >= 1
    assert response.confidence is not None and any(
        "waiting for review" in r for r in response.confidence.reasons
    )
    assert response.answer is not None and response.answer.startswith("There is 1 record [D1].")
    assert "waiting for review" in response.answer and "not counted" in response.answer


def test_bound_scope_holds_even_if_session_settings_change(
    database: Database, ctx_for: Ctx, add_records: Any, set_config_restricted: bool
) -> None:
    """The third barrier: bypass the SQL guard on purpose and switch the session's tenant mid-query."""
    name = add_records("acme", FOUR_DAYS)
    add_records("globex", FOUR_DAYS, source_file=name)
    attack = GuardedSql(
        sql=(
            "SELECT set_config('app.tenant_id', 'globex', true) AS switched, "
            f"(SELECT COUNT(*) FROM v_attendance WHERE source_file = '{name}') AS visible"
        ),
        evidence_sql=None,
    )
    runner = SqlRunner(database, row_cap=10, evidence_cap=10)
    if set_config_restricted:
        with pytest.raises(SqlExecutionError, match="permission denied"):
            runner.run(ctx_for("acme.admin"), attack)
        return
    rows = runner.run(ctx_for("acme.admin"), attack).rows
    assert rows[0]["visible"] == 4  # Acme's rows only; Globex's are still filtered by the bound scope


def test_every_question_is_audited(
    database: Database, directory: Directory, ctx_for: Ctx, admin_url: str
) -> None:
    import psycopg

    from attendance_ai.stores.setup import libpq_url

    ctx = ctx_for("acme.eng.manager")
    _service(database, directory, MockProvider(ScriptedLLM([_plan("SELECT 1")]))).answer(
        ctx, QueryRequest(question="What was the attendance percentage for Sales?")
    )
    with psycopg.connect(libpq_url(admin_url, database.engine.url.database)) as conn:
        row = conn.execute(
            "SELECT event_type, outcome FROM audit_events WHERE request_id = %s", (ctx.request_id,)
        ).fetchone()
    assert row == ("denial", "denied")
    with database.session_for(ctx) as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(QueryLogEntry)
                .where(QueryLogEntry.request_id == ctx.request_id)
            )
            == 1
        )
