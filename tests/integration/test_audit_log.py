"""The audit trail is append-only and tenant-scoped."""

from collections.abc import Callable

import psycopg
import pytest
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import DBAPIError

from attendance_ai.core.access import AccessContext
from attendance_ai.governance.audit import AuditEvent, AuditLog
from attendance_ai.stores.db import Database
from attendance_ai.stores.models import AuditEntry
from attendance_ai.stores.setup import libpq_url

Ctx = Callable[[str], AccessContext]


def test_app_role_can_append_but_never_change_audit_rows(database: Database, ctx_for: Ctx) -> None:
    ctx = ctx_for("acme.admin")
    AuditLog(database).record(
        AuditEvent("system", "append_only_test", "success", details={"token": "abc"}), ctx
    )
    with database.session_for(ctx) as session:
        row = session.execute(select(AuditEntry).where(AuditEntry.request_id == ctx.request_id)).scalar_one()
    assert row.details == {"token": "[redacted]"}
    with pytest.raises(DBAPIError, match="permission denied"):  # noqa: SIM117
        with database.session_for(ctx) as session:
            session.execute(update(AuditEntry).where(AuditEntry.id == row.id).values(outcome="tampered"))
    with pytest.raises(DBAPIError, match="permission denied"):  # noqa: SIM117
        with database.session_for(ctx) as session:
            session.execute(delete(AuditEntry).where(AuditEntry.id == row.id))


def test_only_admins_of_the_same_tenant_read_audit(database: Database, ctx_for: Ctx) -> None:
    admin = ctx_for("acme.admin")
    AuditLog(database).record(AuditEvent("system", "visibility_test", "success"), admin)

    def count(ctx: AccessContext) -> int:
        with database.session_for(ctx) as session:
            query = (
                select(func.count()).select_from(AuditEntry).where(AuditEntry.request_id == admin.request_id)
            )
            return int(session.scalar(query) or 0)

    assert count(admin) == 1
    assert count(ctx_for("acme.eng.manager")) == 0
    assert count(ctx_for("globex.admin")) == 0


def _count_as_superuser(admin_url: str, database: Database, request_id: str) -> int:
    with psycopg.connect(libpq_url(admin_url, database.engine.url.database)) as conn:
        row = conn.execute(
            "SELECT count(*) FROM audit_events WHERE request_id = %s", (request_id,)
        ).fetchone()
    return int(row[0]) if row else 0


def test_events_without_a_tenant_are_still_recorded(database: Database, admin_url: str) -> None:
    request_id = "req_anonymous_audit_01"
    AuditLog(database).record(AuditEvent("auth", "anonymous_test", "denied"), request_id=request_id)
    # No tenant context can read such rows; check as the server superuser instead.
    assert _count_as_superuser(admin_url, database, request_id) == 1


def test_roles_that_cannot_read_audit_can_still_write_it(
    database: Database, ctx_for: Ctx, admin_url: str
) -> None:
    manager = ctx_for("acme.eng.manager")
    AuditLog(database).record(AuditEvent("denial", "tenant_mismatch", "denied"), manager)
    assert _count_as_superuser(admin_url, database, manager.request_id) == 1


def test_missing_optional_fields_are_stored_as_sql_null(
    database: Database, ctx_for: Ctx, admin_url: str
) -> None:
    ctx = ctx_for("acme.admin")
    AuditLog(database).record(AuditEvent("system", "null_fields_test", "success"), ctx)
    with psycopg.connect(libpq_url(admin_url, database.engine.url.database)) as conn:
        row = conn.execute(
            "SELECT retrieved_ids IS NULL, fallback_path IS NULL FROM audit_events WHERE request_id = %s",
            (ctx.request_id,),
        ).fetchone()
    assert row == (True, True)
