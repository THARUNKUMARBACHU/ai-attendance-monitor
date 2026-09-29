import pytest

from attendance_ai.retrieval.context import pack_context, summarize_locations
from attendance_ai.retrieval.documents import DocumentHit
from attendance_ai.retrieval.sql_guard import UnsafeSqlError, guard_sql
from attendance_ai.retrieval.sql_runner import SqlResult, _wrap

SAFE = [
    "SELECT department_name, "
    "ROUND(100.0 * SUM(attendance_weight) / NULLIF(COUNT(attendance_weight), 0), 2) AS pct "
    "FROM v_attendance WHERE attendance_date BETWEEN '2026-08-01' AND '2026-08-31' GROUP BY department_name",
    "SELECT employee_id, status FROM v_attendance a "
    "WHERE a.attendance_date = '2026-08-05' ORDER BY employee_id",
    "WITH d AS (SELECT department_name, SUM(attendance_weight) AS s FROM v_attendance GROUP BY 1) "
    "SELECT * FROM d",
    "SELECT employee_id FROM v_attendance WHERE remarks LIKE '%fever%' OR is_late",
]
ATTACKS = [
    ("SELECT set_config('app.tenant_id', 'globex', true) FROM v_attendance", "set_config"),
    ("SELECT current_setting('app.tenant_id')", "current_setting"),
    ("SELECT * FROM attendance_records", "Only v_attendance"),
    ("SELECT * FROM public.v_attendance", "Schema-qualified"),
    ("SELECT * FROM v_attendance, pg_catalog.pg_roles", "Schema-qualified"),
    ("SELECT pg_sleep(5)", "pg_sleep"),
    ("SELECT query_to_xml('select 1', true, true, '')", "query_to_xml"),
    ("DELETE FROM v_attendance", "Only SELECT"),
    ("SET app.tenant_id = 'globex'", "Only SELECT"),
    ("SELECT 1; SELECT 2", "Exactly one"),
    ("SELECT * FROM v_attendance FOR UPDATE", "locking"),
    ("SELECT * INTO copy FROM v_attendance", "INTO"),
    ("SELECT * FROM generate_series(1, 10)", "Only v_attendance"),
    ("SELECT * FROM v_attendance WHERE employee_id IN (SELECT usename FROM pg_user)", "Only v_attendance"),
    (
        "WITH v_attendance AS (SELECT * FROM attendance_records) SELECT * FROM v_attendance",
        "Only v_attendance",
    ),
    ("SELEC nonsense", "Only SELECT"),
    ("SELECT FROM WHERE (", "parsed"),
]


@pytest.mark.parametrize("sql", SAFE)
def test_guard_allows_analytics_queries_and_derives_evidence(sql: str) -> None:
    guarded = guard_sql(sql)
    assert guarded.evidence_sql is not None
    assert guarded.evidence_sql.upper().startswith("SELECT")
    assert "record_id" in guarded.evidence_sql and "source_page_or_row" in guarded.evidence_sql


@pytest.mark.parametrize(("sql", "reason"), ATTACKS)
def test_guard_blocks_unsafe_sql(sql: str, reason: str) -> None:
    with pytest.raises(UnsafeSqlError, match=reason):
        guard_sql(sql)


def test_evidence_query_keeps_the_filters_and_alias() -> None:
    guarded = guard_sql(SAFE[1])
    assert guarded.evidence_sql is not None
    assert "a.record_id" in guarded.evidence_sql
    assert "a.attendance_date = '2026-08-05'" in guarded.evidence_sql


def test_wrapped_query_binds_scope_and_escapes_colons() -> None:
    sql = _wrap("SELECT check_in::text FROM v_attendance WHERE check_in > '09:30'", 10)
    assert sql.startswith("WITH v_attendance AS MATERIALIZED (")
    assert ":_tenant_id" in sql and ":_entity_scope" in sql and ":_clearance" in sql
    assert "check_in\\:\\:text" in sql and "'09\\:30'" in sql
    assert sql.endswith("LIMIT 10")


def test_summarize_locations_compresses_ranges() -> None:
    assert summarize_locations([f"row {n}" for n in (2, 3, 4, 7)]) == "rows 2-4, 7"
    assert (
        summarize_locations(["page 1, row 1", "page 1, row 2", "page 2, row 1"])
        == "page 1 rows 1-2; page 2 rows 1"
    )
    assert (
        summarize_locations(["sheet 'Aug-2026', cell F5", "sheet 'Aug-2026', cell G6"])
        == "sheet 'Aug-2026' rows 5-6"
    )
    assert summarize_locations(["paragraph 3"]) == "paragraph 3"


def test_pack_context_labels_records_and_documents() -> None:
    result = SqlResult(
        columns=["department_name", "attendance_pct"],
        rows=[{"department_name": "Engineering", "attendance_pct": 92.41}],
        truncated=False,
        evidence=[
            {"source_file": "eng.csv", "source_page_or_row": "row 2"},
            {"source_file": "eng.csv", "source_page_or_row": "row 3"},
        ],
        evidence_available=True,
    )
    hit = DocumentHit(
        "p1", "Field visit to vendor site", "ops.docx", "table 2, row 3", "remark", 0.5, 2.0, None
    )
    context = pack_context(result, [hit])
    assert [citation.id for citation in context.citations] == ["D1", "S1"]
    assert "[D1] eng.csv: rows 2-3 (2 records)" in context.text
    assert "untrusted" in context.text
    assert "92.41" in context.numbers


def test_empty_aggregate_counts_as_no_data() -> None:
    assert SqlResult(columns=["pct"], rows=[{"pct": None, "counted": 0}], truncated=False).is_empty
    assert not SqlResult(columns=["pct"], rows=[{"pct": 90.5}], truncated=False).is_empty
