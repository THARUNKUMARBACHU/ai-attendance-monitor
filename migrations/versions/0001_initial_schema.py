"""Initial schema: tables, row-level security policies and the v_attendance view.

Role names never appear here. Grants are applied by attendance_ai.stores.setup after
migrations run, so the same migrations work for any role naming.

Revision ID: 0001
Revises:
Create Date: 2026-09-27
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

# Row-level security reads the caller's access from per-transaction settings (app.*),
# set by attendance_ai.stores.db. A missing setting raises an error, so queries fail closed.
TENANT_MATCH = (
    "tenant_id = current_setting('app.tenant_id') "
    "AND product_id = current_setting('app.product_id') "
    "AND module = current_setting('app.module')"
)
ADMIN_ONLY = f"{TENANT_MATCH} AND current_setting('app.scope') = 'tenant'"
RECORD_SCOPE = (
    "CASE current_setting('app.scope') "
    "WHEN 'tenant' THEN true "
    "WHEN 'department' THEN entity_id = ANY (string_to_array(current_setting('app.entity_scope'), ',')) "
    "WHEN 'self' THEN employee_id = current_setting('app.employee_id') "
    "ELSE false END"
)
EXAMPLE_SCOPE = (
    "CASE current_setting('app.scope') "
    "WHEN 'tenant' THEN true "
    "WHEN 'department' THEN scope <> 'tenant' "
    "AND entity_scope <@ string_to_array(current_setting('app.entity_scope'), ',') "
    "WHEN 'self' THEN scope = 'self' AND employee_id = current_setting('app.employee_id') "
    "ELSE false END"
)
CLEARANCE_OK = "app_clearance_rank(classification) <= current_setting('app.clearance')::integer"

TABLES = (
    "sources",
    "source_versions",
    "ingestion_jobs",
    "attendance_records",
    "query_log",
    "feedback_examples",
    "scope_versions",
    "audit_events",
)

UPGRADE = [
    """
    CREATE FUNCTION app_clearance_rank(level text) RETURNS integer
    LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
    AS $$
        SELECT CASE level
            WHEN 'public' THEN 0
            WHEN 'internal' THEN 1
            WHEN 'confidential' THEN 2
            WHEN 'restricted' THEN 3
        END
    $$
    """,
    """
    CREATE TABLE sources (
        id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id          text NOT NULL,
        product_id         text NOT NULL,
        module             text NOT NULL,
        entity_id          text,
        classification     text NOT NULL DEFAULT 'confidential'
                           CHECK (app_clearance_rank(classification) IS NOT NULL),
        logical_name       text NOT NULL,
        current_version_id uuid,
        created_by         text NOT NULL,
        created_at         timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT uq_sources_logical_name UNIQUE (tenant_id, product_id, module, logical_name)
    )
    """,
    """
    CREATE TABLE source_versions (
        id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        source_id         uuid NOT NULL REFERENCES sources (id),
        tenant_id         text NOT NULL,
        product_id        text NOT NULL,
        module            text NOT NULL,
        version_no        integer NOT NULL CHECK (version_no > 0),
        checksum          text NOT NULL,
        media_type        text NOT NULL,
        size_bytes        bigint NOT NULL CHECK (size_bytes >= 0),
        storage_path      text NOT NULL,
        original_filename text NOT NULL,
        status            text NOT NULL DEFAULT 'received'
                          CHECK (status IN ('received', 'processing', 'processed', 'failed', 'superseded')),
        created_by        text NOT NULL,
        created_at        timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT uq_source_versions_checksum UNIQUE (tenant_id, product_id, module, checksum),
        CONSTRAINT uq_source_versions_number UNIQUE (source_id, version_no)
    )
    """,
    """
    ALTER TABLE sources
        ADD CONSTRAINT fk_sources_current_version
        FOREIGN KEY (current_version_id) REFERENCES source_versions (id)
    """,
    """
    CREATE TABLE ingestion_jobs (
        id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id         text NOT NULL,
        product_id        text NOT NULL,
        module            text NOT NULL,
        source_version_id uuid REFERENCES source_versions (id),
        requested_by      text NOT NULL,
        status            text NOT NULL DEFAULT 'queued'
                          CHECK (status IN ('queued', 'running', 'completed', 'completed_with_errors',
                                            'failed', 'duplicate')),
        stages            jsonb NOT NULL DEFAULT '[]'::jsonb,
        attempts          integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
        max_attempts      integer NOT NULL DEFAULT 3 CHECK (max_attempts > 0),
        last_error        text,
        counts            jsonb NOT NULL DEFAULT '{}'::jsonb,
        failures          jsonb NOT NULL DEFAULT '[]'::jsonb,
        created_at        timestamptz NOT NULL DEFAULT now(),
        updated_at        timestamptz NOT NULL DEFAULT now(),
        started_at        timestamptz,
        finished_at       timestamptz
    )
    """,
    "CREATE INDEX ix_ingestion_jobs_scope ON ingestion_jobs (tenant_id, product_id, module, created_at DESC)",
    """
    CREATE TABLE attendance_records (
        id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        record_key            text NOT NULL,
        tenant_id             text NOT NULL,
        product_id            text NOT NULL,
        module                text NOT NULL,
        entity_id             text NOT NULL,
        classification        text NOT NULL DEFAULT 'confidential'
                              CHECK (app_clearance_rank(classification) IS NOT NULL),
        department            text,
        employee_id           text NOT NULL,
        employee_name         text,
        attendance_date       date NOT NULL,
        status                text NOT NULL
                              CHECK (status IN ('PRESENT', 'ABSENT', 'LEAVE', 'WFH', 'HALF_DAY',
                                                'HOLIDAY', 'WEEKLY_OFF')),
        raw_status            text,
        check_in              time,
        check_out             time,
        total_hours           numeric(5, 2) CHECK (total_hours IS NULL OR total_hours >= 0),
        remarks               text,
        remarks_sensitivity   text CHECK (remarks_sensitivity IN ('confidential', 'restricted')),
        pii_types             text[] NOT NULL DEFAULT '{}',
        source_id             uuid NOT NULL REFERENCES sources (id),
        source_version_id     uuid NOT NULL REFERENCES source_versions (id),
        source_file           text NOT NULL,
        source_locator        jsonb NOT NULL,
        source_page_or_row    text NOT NULL,
        extraction_method     text NOT NULL
                              CHECK (extraction_method IN ('native_csv', 'native_xlsx', 'native_docx',
                                                           'pdf_text', 'ocr')),
        extraction_confidence numeric(4, 3) NOT NULL DEFAULT 1.000
                              CHECK (extraction_confidence BETWEEN 0 AND 1),
        field_confidence      jsonb,
        review_status         text NOT NULL DEFAULT 'auto_accepted'
                              CHECK (review_status IN ('auto_accepted', 'needs_review', 'verified', 'rejected')),
        validation_flags      text[] NOT NULL DEFAULT '{}',
        is_active             boolean NOT NULL DEFAULT true,
        superseded_by         uuid REFERENCES attendance_records (id),
        created_at            timestamptz NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE UNIQUE INDEX uq_attendance_active_day
        ON attendance_records (tenant_id, product_id, module, employee_id, attendance_date)
        WHERE is_active
    """,
    "CREATE INDEX ix_attendance_scope_date ON attendance_records (tenant_id, product_id, module, attendance_date)",
    "CREATE INDEX ix_attendance_scope_entity ON attendance_records (tenant_id, product_id, module, entity_id)",
    "CREATE INDEX ix_attendance_source_version ON attendance_records (source_version_id)",
    """
    CREATE TABLE query_log (
        request_id      text PRIMARY KEY,
        tenant_id       text NOT NULL,
        product_id      text NOT NULL,
        module          text NOT NULL,
        user_id         text NOT NULL,
        role            text NOT NULL,
        question_masked text NOT NULL,
        mode            text,
        plan            jsonb,
        sql_text        text,
        retrieved_ids   jsonb NOT NULL DEFAULT '[]'::jsonb,
        answer          jsonb,
        confidence      numeric(4, 3),
        outcome         text NOT NULL,
        versions        jsonb NOT NULL DEFAULT '{}'::jsonb,
        created_at      timestamptz NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX ix_query_log_scope ON query_log (tenant_id, product_id, module, created_at DESC)",
    """
    CREATE TABLE feedback_examples (
        id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id          text NOT NULL,
        product_id         text NOT NULL,
        module             text NOT NULL,
        scope              text NOT NULL CHECK (scope IN ('tenant', 'department', 'self')),
        entity_scope       text[] NOT NULL DEFAULT '{}',
        employee_id        text,
        clearance          integer NOT NULL CHECK (clearance BETWEEN 0 AND 3),
        request_id         text NOT NULL,
        question           text NOT NULL,
        question_embedding real[],
        original_answer    jsonb NOT NULL,
        feedback           text NOT NULL,
        ideal_output       text NOT NULL,
        status             text NOT NULL CHECK (status IN ('active', 'inactive', 'rejected')),
        version            integer NOT NULL DEFAULT 1,
        reviewer_id        text NOT NULL,
        approval_note      text,
        validation         jsonb NOT NULL DEFAULT '{}'::jsonb,
        versions           jsonb NOT NULL DEFAULT '{}'::jsonb,
        created_at         timestamptz NOT NULL DEFAULT now(),
        deactivated_at     timestamptz,
        deactivated_by     text
    )
    """,
    "CREATE INDEX ix_feedback_scope_status ON feedback_examples (tenant_id, product_id, module, status)",
    """
    CREATE TABLE scope_versions (
        tenant_id         text NOT NULL,
        product_id        text NOT NULL,
        module            text NOT NULL,
        data_version      bigint NOT NULL DEFAULT 0,
        knowledge_version bigint NOT NULL DEFAULT 0,
        updated_at        timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (tenant_id, product_id, module)
    )
    """,
    """
    CREATE TABLE audit_events (
        id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        occurred_at   timestamptz NOT NULL DEFAULT now(),
        request_id    text,
        tenant_id     text,
        product_id    text,
        module        text,
        user_id       text,
        role          text,
        event_type    text NOT NULL
                      CHECK (event_type IN ('auth', 'query', 'ingest', 'export', 'feedback',
                                            'denial', 'security', 'system')),
        action        text NOT NULL,
        outcome       text NOT NULL,
        query_mode    text,
        retrieved_ids jsonb,
        provider      text,
        model         text,
        fallback_path jsonb,
        confidence    numeric(4, 3),
        latency_ms    integer,
        details       jsonb NOT NULL DEFAULT '{}'::jsonb
    )
    """,
    "CREATE INDEX ix_audit_events_scope_time ON audit_events (tenant_id, occurred_at DESC)",
    *[f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY" for table in TABLES],
    *[f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY" for table in TABLES],
    f"CREATE POLICY sources_isolation ON sources USING ({ADMIN_ONLY}) WITH CHECK ({ADMIN_ONLY})",
    f"CREATE POLICY source_versions_isolation ON source_versions USING ({ADMIN_ONLY}) WITH CHECK ({ADMIN_ONLY})",
    f"CREATE POLICY ingestion_jobs_isolation ON ingestion_jobs USING ({ADMIN_ONLY}) WITH CHECK ({ADMIN_ONLY})",
    f"""
    CREATE POLICY attendance_read ON attendance_records FOR SELECT
        USING ({TENANT_MATCH} AND {CLEARANCE_OK} AND {RECORD_SCOPE})
    """,
    f"CREATE POLICY attendance_insert ON attendance_records FOR INSERT WITH CHECK ({ADMIN_ONLY})",
    f"""
    CREATE POLICY attendance_update ON attendance_records FOR UPDATE
        USING ({ADMIN_ONLY}) WITH CHECK ({ADMIN_ONLY})
    """,
    f"""
    CREATE POLICY query_log_read ON query_log FOR SELECT
        USING ({TENANT_MATCH} AND (current_setting('app.scope') = 'tenant'
                                   OR user_id = current_setting('app.user_id')))
    """,
    f"""
    CREATE POLICY query_log_insert ON query_log FOR INSERT
        WITH CHECK ({TENANT_MATCH} AND user_id = current_setting('app.user_id'))
    """,
    f"""
    CREATE POLICY query_log_update ON query_log FOR UPDATE
        USING ({TENANT_MATCH} AND user_id = current_setting('app.user_id'))
        WITH CHECK ({TENANT_MATCH} AND user_id = current_setting('app.user_id'))
    """,
    f"""
    CREATE POLICY feedback_read ON feedback_examples FOR SELECT
        USING ({TENANT_MATCH} AND clearance <= current_setting('app.clearance')::integer AND {EXAMPLE_SCOPE})
    """,
    f"CREATE POLICY feedback_insert ON feedback_examples FOR INSERT WITH CHECK ({ADMIN_ONLY})",
    f"""
    CREATE POLICY feedback_update ON feedback_examples FOR UPDATE
        USING ({ADMIN_ONLY}) WITH CHECK ({ADMIN_ONLY})
    """,
    f"CREATE POLICY scope_versions_isolation ON scope_versions USING ({TENANT_MATCH}) WITH CHECK ({TENANT_MATCH})",
    """
    CREATE POLICY audit_insert ON audit_events FOR INSERT
        WITH CHECK (tenant_id IS NULL OR tenant_id = current_setting('app.tenant_id', true))
    """,
    """
    CREATE POLICY audit_read ON audit_events FOR SELECT
        USING (tenant_id = current_setting('app.tenant_id') AND current_setting('app.scope') = 'tenant')
    """,
    # The only relation LLM-written SQL may read. It is owned by the table owner and is not
    # security_invoker, so the query role needs no access to attendance_records itself.
    # FORCE ROW LEVEL SECURITY makes the policies apply through the view, using the caller's
    # session settings. Remarks are masked below restricted clearance; records that are
    # inactive, rejected or waiting for review are left out.
    """
    CREATE VIEW v_attendance WITH (security_barrier = true) AS
    SELECT
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
        r.extraction_method
    FROM attendance_records r
    WHERE r.is_active AND r.review_status IN ('auto_accepted', 'verified')
    """,
]

DOWNGRADE = [
    "DROP VIEW IF EXISTS v_attendance",
    "DROP TABLE IF EXISTS audit_events",
    "DROP TABLE IF EXISTS scope_versions",
    "DROP TABLE IF EXISTS feedback_examples",
    "DROP TABLE IF EXISTS query_log",
    "DROP TABLE IF EXISTS attendance_records",
    "DROP TABLE IF EXISTS ingestion_jobs",
    "ALTER TABLE IF EXISTS sources DROP CONSTRAINT IF EXISTS fk_sources_current_version",
    "DROP TABLE IF EXISTS source_versions",
    "DROP TABLE IF EXISTS sources",
    "DROP FUNCTION IF EXISTS app_clearance_rank(text)",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
