"""Per-scope version counters: the data version changes on every ingestion, the knowledge version
on every feedback change. Answers record both, and cache keys include them."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from attendance_ai.core.access import AccessContext
from attendance_ai.stores.models import ScopeVersion


@dataclass(frozen=True, slots=True)
class Versions:
    data: int
    knowledge: int


def current_versions(session: Session, ctx: AccessContext) -> Versions:
    row = session.execute(
        select(ScopeVersion.data_version, ScopeVersion.knowledge_version).where(
            ScopeVersion.tenant_id == ctx.tenant_id,
            ScopeVersion.product_id == ctx.product_id,
            ScopeVersion.module == ctx.module,
        )
    ).one_or_none()
    return Versions(data=row[0], knowledge=row[1]) if row else Versions(data=0, knowledge=0)


def bump_data_version(session: Session, ctx: AccessContext) -> None:
    session.execute(
        insert(ScopeVersion)
        .values(tenant_id=ctx.tenant_id, product_id=ctx.product_id, module=ctx.module, data_version=1)
        .on_conflict_do_nothing(index_elements=["tenant_id", "product_id", "module"])
    )
    session.execute(
        update(ScopeVersion)
        .where(
            ScopeVersion.tenant_id == ctx.tenant_id,
            ScopeVersion.product_id == ctx.product_id,
            ScopeVersion.module == ctx.module,
        )
        .values(data_version=ScopeVersion.data_version + 1)
    )


def bump_knowledge_version(session: Session, ctx: AccessContext) -> int:
    """Record a change to the approved feedback of this scope; returns the new knowledge version. Cache
    keys include it, so cached answers made before the change are never served again."""
    session.execute(
        insert(ScopeVersion)
        .values(tenant_id=ctx.tenant_id, product_id=ctx.product_id, module=ctx.module, knowledge_version=0)
        .on_conflict_do_nothing(index_elements=["tenant_id", "product_id", "module"])
    )
    version = session.execute(
        update(ScopeVersion)
        .where(
            ScopeVersion.tenant_id == ctx.tenant_id,
            ScopeVersion.product_id == ctx.product_id,
            ScopeVersion.module == ctx.module,
        )
        .values(knowledge_version=ScopeVersion.knowledge_version + 1, updated_at=func.now())
        .returning(ScopeVersion.knowledge_version)
    ).scalar_one()
    return int(version)
