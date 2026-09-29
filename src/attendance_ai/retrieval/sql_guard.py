"""Checks LLM-written SQL before it runs. Deny by default:

- exactly one read-only query (SELECT, optionally with CTEs or set operations);
- the only relation it may read is ``v_attendance`` (plus CTEs it defines), with no schema prefix;
- only allow-listed functions, so nothing like set_config(), pg_sleep() or query_to_xml() can run;
- no locking clauses and no SELECT INTO.

The guard is one of three barriers: the query also runs as a read-only role under row-level security,
inside a CTE that binds the caller's scope as parameters (retrieval/sql_runner.py).
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from attendance_ai.core.errors import AppError

VIEW = "v_attendance"
ALLOWED_FUNCTIONS = frozenset(
    {
        "ABS",
        "AVG",
        "CAST",
        "COALESCE",
        "COUNT",
        "CURRENT_DATE",
        "DATE_PART",
        "DATE_TRUNC",
        "DENSE_RANK",
        "EXTRACT",
        "GREATEST",
        "GROUP_CONCAT",
        "LEAST",
        "LENGTH",
        "LOWER",
        "MAX",
        "MIN",
        "NULLIF",
        "RANK",
        "ROUND",
        "ROW_NUMBER",
        "STRING_AGG",
        "SUM",
        "TIMESTAMP_TRUNC",
        "TIME_TO_STR",
        "TO_CHAR",
        "TRIM",
        "UPPER",
    }
)
EVIDENCE_COLUMNS = (
    "record_id",
    "attendance_date",
    "department_id",
    "source_file",
    "source_page_or_row",
    "extraction_method",
    "extraction_confidence",
)
_QUERY_TYPES = (exp.Select, exp.Union, exp.Intersect, exp.Except)


class UnsafeSqlError(AppError):
    status_code = 422
    code = "unsafe_sql"
    title = "Query rejected"


@dataclass(frozen=True, slots=True)
class GuardedSql:
    sql: str
    evidence_sql: str | None


def guard_sql(sql: str) -> GuardedSql:
    """Validate and normalise the SQL; raises UnsafeSqlError with the reason when it is not allowed."""
    try:
        statements = [statement for statement in sqlglot.parse(sql, read="postgres") if statement is not None]
    except ParseError as exc:
        raise UnsafeSqlError(f"The query could not be parsed: {str(exc).splitlines()[0]}") from exc
    if len(statements) != 1:
        raise UnsafeSqlError("Exactly one statement is allowed.")
    tree = statements[0]
    if not isinstance(tree, _QUERY_TYPES):
        raise UnsafeSqlError("Only SELECT queries are allowed.")
    if tree.find(exp.Into) or tree.find(exp.Lock):
        raise UnsafeSqlError("SELECT INTO and locking clauses are not allowed.")

    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    for table in tree.find_all(exp.Table):
        if table.args.get("db") or table.args.get("catalog"):
            raise UnsafeSqlError("Schema-qualified names are not allowed.")
        name = table.name.lower()
        if not isinstance(table.this, exp.Identifier) or (name != VIEW and name not in cte_names):
            raise UnsafeSqlError(f"Only {VIEW} can be queried.")
    for function in tree.find_all(exp.Func):
        if isinstance(function, exp.Binary | exp.Unary):
            continue  # operators such as AND / OR, which sqlglot also models as functions
        name = (function.name if isinstance(function, exp.Anonymous) else function.sql_name()).upper()
        if name not in ALLOWED_FUNCTIONS:
            raise UnsafeSqlError(f"Function {name.lower()}() is not allowed.")

    return GuardedSql(sql=tree.sql(dialect="postgres"), evidence_sql=_evidence_query(tree))


def _evidence_query(tree: exp.Expression) -> str | None:
    """The rows that fed the calculation: the innermost SELECT reading v_attendance directly, with its
    WHERE clause, projected onto provenance columns. None when that cannot be derived safely."""
    readers = [
        (select, source)
        for select in tree.find_all(exp.Select)
        if (source := _from_table(select)) is not None and source.name.lower() == VIEW
    ]
    if not readers:
        return None
    select, source = readers[-1]
    if select.args.get("joins"):
        return None
    alias = source.alias or None
    columns = [exp.column(name, table=alias) for name in EVIDENCE_COLUMNS]
    evidence = exp.select(*columns).from_(source.copy())
    where = select.args.get("where")
    if where is not None:
        evidence = evidence.where(where.this.copy())
    return evidence.sql(dialect="postgres")


def _from_table(select: exp.Select) -> exp.Table | None:
    clause = select.args.get("from_") or select.args.get("from")  # the key name differs across versions
    if isinstance(clause, exp.From) and isinstance(clause.this, exp.Table):
        return clause.this
    return None
