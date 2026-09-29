"""Expose scope columns on v_attendance and allow an unreadable status on records awaiting review.

- The query executor binds the caller's scope as parameters in a CTE over v_attendance. That second
  barrier (after row-level security) needs tenant, product and module columns on the view, plus a
  flag for restricted remarks so masking can also be applied from bound parameters.
- An OCR cell may be unreadable. Such a record is kept for review with a NULL status; it is never
  queryable, because the view only returns reviewed or auto-accepted records.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-28
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

VIEW_COLUMNS = """
        r.id                 AS record_id,
        r.attendance_date,
        r.employee_id,
        r.employee_name,
        r.entity_id          AS department_id,
        r.department         AS department_name,
        r.status,
        r.check_in,
        r.check_out,
        r.total_hours,
        (r.check_in IS NOT NULL AND r.check_in > current_setting('app.late_after')::time) AS is_late,
        (current_setting('app.weights')::jsonb ->> r.status)::numeric AS attendance_weight,
        ((current_setting('app.weights')::jsonb ->> r.status) IS NOT NULL) AS is_counted,
        CASE
            WHEN r.remarks_sensitivity = 'restricted' AND current_setting('app.clearance')::integer < 3
                THEN '[restricted]'
            ELSE r.remarks
        END                  AS remarks,
        r.source_file,
        r.source_page_or_row,
        r.extraction_method"""

VIEW_FILTER = "FROM attendance_records r WHERE r.is_active AND r.review_status IN ('auto_accepted', 'verified')"

UPGRADE = [
    "ALTER TABLE attendance_records ALTER COLUMN status DROP NOT NULL",
    """
    ALTER TABLE attendance_records ADD CONSTRAINT ck_attendance_status_known
        CHECK (status IS NOT NULL OR review_status = 'needs_review')
    """,
    f"""
    CREATE OR REPLACE VIEW v_attendance WITH (security_barrier = true) AS
    SELECT {VIEW_COLUMNS},
        r.tenant_id,
        r.product_id,
        r.module,
        COALESCE(r.remarks_sensitivity = 'restricted', false) AS remarks_restricted,
        r.extraction_confidence
    {VIEW_FILTER}
    """,
]

DOWNGRADE = [
    "DROP VIEW IF EXISTS v_attendance",
    f"CREATE VIEW v_attendance WITH (security_barrier = true) AS SELECT {VIEW_COLUMNS} {VIEW_FILTER}",
    "ALTER TABLE attendance_records DROP CONSTRAINT IF EXISTS ck_attendance_status_known",
    "ALTER TABLE attendance_records ALTER COLUMN status SET NOT NULL",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    # Re-run scripts/db_setup.py afterwards: dropping the view removes its grants.
    for statement in DOWNGRADE:
        op.execute(statement)
