"""Isolation is enforced by PostgreSQL itself, before any data reaches application code."""

from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy import insert, select
from sqlalchemy.exc import DBAPIError

from attendance_ai.core.access import AccessContext
from attendance_ai.stores.db import Database
from attendance_ai.stores.models import AttendanceRecord

Ctx = Callable[[str], AccessContext]


def visible(database: Database, ctx: AccessContext, source_file: str) -> list[AttendanceRecord]:
    with database.session_for(ctx) as session:
        query = select(AttendanceRecord).where(AttendanceRecord.source_file == source_file)
        return list(session.scalars(query))


def test_tenants_never_see_each_others_records(database: Database, ctx_for: Ctx, add_records: Any) -> None:
    name = add_records("acme", [{"employee_id": "E001"}])
    add_records("globex", [{"employee_id": "E001"}], source_file=name)  # same file name and employee ID
    assert [r.tenant_id for r in visible(database, ctx_for("acme.admin"), name)] == ["acme"]
    assert [r.tenant_id for r in visible(database, ctx_for("globex.admin"), name)] == ["globex"]


def test_manager_sees_only_their_departments(database: Database, ctx_for: Ctx, add_records: Any) -> None:
    name = add_records(
        "acme", [{"entity_id": "ENG", "employee_id": "E002"}, {"entity_id": "SAL", "employee_id": "E007"}]
    )
    assert {r.entity_id for r in visible(database, ctx_for("acme.eng.manager"), name)} == {"ENG"}
    assert len(visible(database, ctx_for("acme.admin"), name)) == 2


def test_employee_sees_only_their_own_records(database: Database, ctx_for: Ctx, add_records: Any) -> None:
    name = add_records(
        "acme", [{"entity_id": "SAL", "employee_id": "E006"}, {"entity_id": "SAL", "employee_id": "E007"}]
    )
    assert [r.employee_id for r in visible(database, ctx_for("acme.employee"), name)] == ["E006"]


def test_clearance_hides_restricted_records(database: Database, ctx_for: Ctx, add_records: Any) -> None:
    name = add_records(
        "acme",
        [{"classification": "restricted"}, {"classification": "confidential", "employee_id": "E002"}],
    )
    assert len(visible(database, ctx_for("acme.eng.manager"), name)) == 1
    assert len(visible(database, ctx_for("acme.admin"), name)) == 2


def test_missing_access_context_fails_closed(database: Database, add_records: Any) -> None:
    name = add_records("acme", [{}])
    try:
        with database.session() as session:
            rows = list(session.scalars(select(AttendanceRecord).where(AttendanceRecord.source_file == name)))
    except DBAPIError:
        return  # an unset context raises inside PostgreSQL: also closed
    assert rows == []


def test_cannot_write_rows_for_another_tenant(
    database: Database, system_ctx: Ctx, new_source: Any, record_values: Any
) -> None:
    source_id, version_id, name = new_source("acme")
    with pytest.raises(DBAPIError, match="row-level security"):  # noqa: SIM117
        with database.session_for(system_ctx("acme")) as session:
            session.execute(
                insert(AttendanceRecord).values(**record_values("globex", source_id, version_id, name))
            )


def test_managers_cannot_write_records(
    database: Database, ctx_for: Ctx, new_source: Any, record_values: Any
) -> None:
    source_id, version_id, name = new_source("acme")
    with pytest.raises(DBAPIError, match="row-level security"):  # noqa: SIM117
        with database.session_for(ctx_for("acme.eng.manager")) as session:
            session.execute(
                insert(AttendanceRecord).values(**record_values("acme", source_id, version_id, name))
            )
