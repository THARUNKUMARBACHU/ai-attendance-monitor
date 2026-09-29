"""SQLAlchemy models. The schema itself is owned by the Alembic migrations in migrations/versions;
these classes mirror it for typed queries (tests/integration checks the two stay in sync)."""

from __future__ import annotations

import uuid
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, Identity, Integer, Numeric, Text, Time, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, REAL, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


# Store Python None as SQL NULL rather than the JSON value null.
NULLABLE_JSONB = JSONB(none_as_null=True)


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=text("now()"))


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[str] = mapped_column(Text)
    product_id: Mapped[str] = mapped_column(Text)
    module: Mapped[str] = mapped_column(Text)
    entity_id: Mapped[str | None] = mapped_column(Text)
    classification: Mapped[str] = mapped_column(Text, server_default=text("'confidential'"))
    logical_name: Mapped[str] = mapped_column(Text)
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source_versions.id", use_alter=True)
    )
    created_by: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()


class SourceVersion(Base):
    __tablename__ = "source_versions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("sources.id"))
    tenant_id: Mapped[str] = mapped_column(Text)
    product_id: Mapped[str] = mapped_column(Text)
    module: Mapped[str] = mapped_column(Text)
    version_no: Mapped[int] = mapped_column(Integer)
    checksum: Mapped[str] = mapped_column(Text)
    media_type: Mapped[str] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    storage_path: Mapped[str] = mapped_column(Text)
    original_filename: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default=text("'received'"))
    created_by: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()


class IngestionJob(Base):
    __tablename__ = "ingestion_jobs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[str] = mapped_column(Text)
    product_id: Mapped[str] = mapped_column(Text)
    module: Mapped[str] = mapped_column(Text)
    source_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source_versions.id")
    )
    requested_by: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default=text("'queued'"))
    stages: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    max_attempts: Mapped[int] = mapped_column(Integer, server_default=text("3"))
    last_error: Mapped[str | None] = mapped_column(Text)
    counts: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    failures: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _created_at()
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AttendanceRecord(Base):
    __tablename__ = "attendance_records"

    id: Mapped[uuid.UUID] = _uuid_pk()
    record_key: Mapped[str] = mapped_column(Text)
    tenant_id: Mapped[str] = mapped_column(Text)
    product_id: Mapped[str] = mapped_column(Text)
    module: Mapped[str] = mapped_column(Text)
    entity_id: Mapped[str] = mapped_column(Text)
    classification: Mapped[str] = mapped_column(Text, server_default=text("'confidential'"))
    department: Mapped[str | None] = mapped_column(Text)
    employee_id: Mapped[str] = mapped_column(Text)
    employee_name: Mapped[str | None] = mapped_column(Text)
    attendance_date: Mapped[date] = mapped_column(Date)
    # NULL only for an unreadable value on a record awaiting review (see migration 0002).
    status: Mapped[str | None] = mapped_column(Text)
    raw_status: Mapped[str | None] = mapped_column(Text)
    check_in: Mapped[time | None] = mapped_column(Time)
    check_out: Mapped[time | None] = mapped_column(Time)
    total_hours: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    remarks: Mapped[str | None] = mapped_column(Text)
    remarks_sensitivity: Mapped[str | None] = mapped_column(Text)
    pii_types: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("sources.id"))
    source_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("source_versions.id"))
    source_file: Mapped[str] = mapped_column(Text)
    source_locator: Mapped[dict[str, Any]] = mapped_column(JSONB)
    source_page_or_row: Mapped[str] = mapped_column(Text)
    extraction_method: Mapped[str] = mapped_column(Text)
    extraction_confidence: Mapped[Decimal] = mapped_column(Numeric(4, 3), server_default=text("1.000"))
    field_confidence: Mapped[dict[str, Any] | None] = mapped_column(NULLABLE_JSONB)
    review_status: Mapped[str] = mapped_column(Text, server_default=text("'auto_accepted'"))
    validation_flags: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
    is_active: Mapped[bool] = mapped_column(server_default=text("true"))
    superseded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("attendance_records.id")
    )
    created_at: Mapped[datetime] = _created_at()


class QueryLogEntry(Base):
    __tablename__ = "query_log"

    request_id: Mapped[str] = mapped_column(Text, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(Text)
    product_id: Mapped[str] = mapped_column(Text)
    module: Mapped[str] = mapped_column(Text)
    user_id: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(Text)
    question_masked: Mapped[str] = mapped_column(Text)
    mode: Mapped[str | None] = mapped_column(Text)
    plan: Mapped[dict[str, Any] | None] = mapped_column(NULLABLE_JSONB)
    sql_text: Mapped[str | None] = mapped_column(Text)
    retrieved_ids: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    answer: Mapped[dict[str, Any] | None] = mapped_column(NULLABLE_JSONB)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    outcome: Mapped[str] = mapped_column(Text)
    versions: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = _created_at()


class FeedbackExample(Base):
    __tablename__ = "feedback_examples"

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[str] = mapped_column(Text)
    product_id: Mapped[str] = mapped_column(Text)
    module: Mapped[str] = mapped_column(Text)
    scope: Mapped[str] = mapped_column(Text)
    entity_scope: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
    employee_id: Mapped[str | None] = mapped_column(Text)
    clearance: Mapped[int] = mapped_column(Integer)
    request_id: Mapped[str] = mapped_column(Text)
    question: Mapped[str] = mapped_column(Text)
    question_embedding: Mapped[list[float] | None] = mapped_column(ARRAY(REAL))
    original_answer: Mapped[dict[str, Any]] = mapped_column(JSONB)
    feedback: Mapped[str] = mapped_column(Text)
    ideal_output: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    reviewer_id: Mapped[str] = mapped_column(Text)
    approval_note: Mapped[str | None] = mapped_column(Text)
    validation: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    versions: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = _created_at()
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deactivated_by: Mapped[str | None] = mapped_column(Text)


class ScopeVersion(Base):
    __tablename__ = "scope_versions"

    tenant_id: Mapped[str] = mapped_column(Text, primary_key=True)
    product_id: Mapped[str] = mapped_column(Text, primary_key=True)
    module: Mapped[str] = mapped_column(Text, primary_key=True)
    data_version: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    knowledge_version: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    updated_at: Mapped[datetime] = _created_at()


class AuditEntry(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    occurred_at: Mapped[datetime] = _created_at()
    request_id: Mapped[str | None] = mapped_column(Text)
    tenant_id: Mapped[str | None] = mapped_column(Text)
    product_id: Mapped[str | None] = mapped_column(Text)
    module: Mapped[str | None] = mapped_column(Text)
    user_id: Mapped[str | None] = mapped_column(Text)
    role: Mapped[str | None] = mapped_column(Text)
    event_type: Mapped[str] = mapped_column(Text)
    action: Mapped[str] = mapped_column(Text)
    outcome: Mapped[str] = mapped_column(Text)
    query_mode: Mapped[str | None] = mapped_column(Text)
    retrieved_ids: Mapped[list[str] | None] = mapped_column(NULLABLE_JSONB)
    provider: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(Text)
    fallback_path: Mapped[list[str] | None] = mapped_column(NULLABLE_JSONB)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
