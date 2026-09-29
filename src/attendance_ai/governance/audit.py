"""Audit trail. Every security-relevant event is written to the append-only audit_events table and
to the structured log, so a failed database write still leaves a record."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from sqlalchemy import Table, insert
from sqlalchemy.exc import SQLAlchemyError

from attendance_ai.core.access import AccessContext
from attendance_ai.core.logging import redact
from attendance_ai.stores.db import Database
from attendance_ai.stores.models import AuditEntry

logger = logging.getLogger(__name__)

_AUDIT_TABLE: Table = AuditEntry.__table__  # type: ignore[assignment]

EventType = Literal["auth", "query", "ingest", "export", "feedback", "denial", "security", "system"]


@dataclass(frozen=True, slots=True)
class AuditEvent:
    event_type: EventType
    action: str
    outcome: str
    details: dict[str, Any] = field(default_factory=dict)
    query_mode: str | None = None
    retrieved_ids: list[str] | None = None
    provider: str | None = None
    model: str | None = None
    fallback_path: list[str] | None = None
    confidence: float | None = None
    latency_ms: int | None = None


class AuditSink(Protocol):
    def record(
        self, event: AuditEvent, ctx: AccessContext | None = None, *, request_id: str | None = None
    ) -> None: ...


class AuditLog:
    """Writes audit events. A failed write is logged loudly but never fails the caller's request."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def record(
        self, event: AuditEvent, ctx: AccessContext | None = None, *, request_id: str | None = None
    ) -> None:
        row: dict[str, Any] = {
            "request_id": ctx.request_id if ctx else request_id,
            "tenant_id": ctx.tenant_id if ctx else None,
            "product_id": ctx.product_id if ctx else None,
            "module": ctx.module if ctx else None,
            "user_id": ctx.user_id if ctx else None,
            "role": ctx.role if ctx else None,
            "event_type": event.event_type,
            "action": event.action,
            "outcome": event.outcome,
            "query_mode": event.query_mode,
            "retrieved_ids": event.retrieved_ids,
            "provider": event.provider,
            "model": event.model,
            "fallback_path": event.fallback_path,
            "confidence": event.confidence,
            "latency_ms": event.latency_ms,
            "details": redact(event.details),
        }
        logger.info(
            "audit_event", extra={"fields": {"audit": {k: v for k, v in row.items() if v is not None}}}
        )
        # An inline INSERT: no RETURNING and no sequence pre-fetch. Most roles may append audit rows
        # but not read them back, and PostgreSQL checks returned rows against the read policy.
        statement = insert(_AUDIT_TABLE).inline().values(**row)
        try:
            if ctx is None:
                with self._database.session() as session:
                    session.execute(statement)
            else:
                with self._database.session_for(ctx) as session:
                    session.execute(statement)
        except SQLAlchemyError:
            logger.exception("audit_write_failed", extra={"fields": {"action": event.action}})
