"""The v_attendance view and the read-only query role that LLM-written SQL will use."""

from collections.abc import Callable
from datetime import time
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from attendance_ai.core.access import AccessContext
from attendance_ai.stores.db import Database

Ctx = Callable[[str], AccessContext]

VIEW_QUERY = text(
    "SELECT employee_id, status, attendance_weight, is_counted, is_late, remarks "
    "FROM v_attendance WHERE source_file = :name ORDER BY employee_id"
)


def view_rows(database: Database, ctx: AccessContext, name: str) -> list[dict[str, Any]]:
    with database.session_for(ctx) as session:
        return [dict(row) for row in session.execute(VIEW_QUERY, {"name": name}).mappings()]


def test_restricted_remarks_are_masked_below_admin(
    database: Database, ctx_for: Ctx, add_records: Any
) -> None:
    remark = "Sick leave - viral fever, medical certificate submitted"
    name = add_records(
        "acme",
        [{"employee_id": "E003", "status": "LEAVE", "remarks": remark, "remarks_sensitivity": "restricted"}],
    )
    assert view_rows(database, ctx_for("acme.eng.manager"), name)[0]["remarks"] == "[restricted]"
    assert view_rows(database, ctx_for("acme.admin"), name)[0]["remarks"] == remark


def test_view_applies_tenant_formula_and_lateness(database: Database, ctx_for: Ctx, add_records: Any) -> None:
    name = add_records(
        "acme",
        [
            {"employee_id": "E001", "status": "PRESENT", "check_in": time(9, 45)},
            {"employee_id": "E002", "status": "HALF_DAY", "check_in": time(9, 0)},
            {"employee_id": "E003", "status": "HOLIDAY"},
        ],
    )
    rows = {row["employee_id"]: row for row in view_rows(database, ctx_for("acme.admin"), name)}
    assert rows["E001"]["attendance_weight"] == Decimal("1.0") and rows["E001"]["is_late"] is True
    assert rows["E002"]["attendance_weight"] == Decimal("0.5") and rows["E002"]["is_late"] is False
    assert rows["E003"]["attendance_weight"] is None and rows["E003"]["is_counted"] is False


def test_records_pending_review_are_left_out(database: Database, ctx_for: Ctx, add_records: Any) -> None:
    name = add_records(
        "acme", [{"employee_id": "E019", "review_status": "needs_review"}, {"employee_id": "E018"}]
    )
    assert [row["employee_id"] for row in view_rows(database, ctx_for("acme.admin"), name)] == ["E018"]


def test_query_role_reads_the_view_within_scope(database: Database, ctx_for: Ctx, add_records: Any) -> None:
    name = add_records(
        "acme", [{"entity_id": "ENG", "employee_id": "E002"}, {"entity_id": "SAL", "employee_id": "E007"}]
    )
    with database.query_connection(ctx_for("acme.eng.manager")) as conn:
        rows = conn.execute(text("SELECT employee_id FROM v_attendance WHERE source_file = :n"), {"n": name})
        assert list(rows.scalars()) == ["E002"]


def test_query_role_cannot_read_the_base_table(database: Database, ctx_for: Ctx) -> None:
    with pytest.raises(DBAPIError, match="permission denied"):  # noqa: SIM117
        with database.query_connection(ctx_for("acme.admin")) as conn:
            conn.execute(text("SELECT count(*) FROM attendance_records"))


def test_query_role_cannot_change_its_access_context(
    database: Database, ctx_for: Ctx, set_config_restricted: bool
) -> None:
    if not set_config_restricted:
        pytest.skip(
            "This server has no superuser, so provisioning could not revoke set_config from the query "
            "role; the SQL guard is the barrier here (see docs/design.md, section 11)."
        )
    with pytest.raises(DBAPIError, match="permission denied for function set_config"):  # noqa: SIM117
        with database.query_connection(ctx_for("acme.admin")) as conn:
            conn.execute(text("SELECT set_config('app.tenant_id', 'globex', true)"))


def test_query_role_is_read_only(database: Database, ctx_for: Ctx) -> None:
    with pytest.raises(DBAPIError, match=r"read-only transaction|permission denied"):  # noqa: SIM117
        with database.query_connection(ctx_for("acme.admin")) as conn:
            conn.execute(text("CREATE TEMP TABLE scratch (x integer)"))
