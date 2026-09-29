"""Qdrant vector store for document search: one collection for all tenants, dense and BM25 vectors.

Isolation: every search goes through ``search(ctx, ...)``, which builds the filter from the access
context itself (callers cannot pass their own), and every returned point is re-checked against the
context before anything is used. A mismatch fails the request closed.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from qdrant_client import QdrantClient, models

from attendance_ai.core.access import AccessContext, Scope, clearance_rank
from attendance_ai.core.config import Settings
from attendance_ai.stores.embeddings import Embedding

logger = logging.getLogger(__name__)

DENSE = "dense"
SPARSE = "bm25"
TENANT_WIDE = "*"  # entity_id of documents not tied to one department: visible to tenant-wide roles only


@dataclass(frozen=True, slots=True)
class Chunk:
    """One searchable piece of text with its isolation context."""

    chunk_id: str
    text: str
    chunk_type: Literal["remark", "narrative"]
    entity_id: str
    classification: str
    location: str
    locator: dict[str, Any]
    employee_id: str | None = None
    attendance_date: str | None = None
    record_id: str | None = None
    heading: str | None = None
    quarantined: bool = False
    injection_signals: tuple[str, ...] = ()


_KEYWORD_FIELDS = (
    "product_id",
    "module",
    "entity_id",
    "employee_id",
    "chunk_type",
    "classification",
    "source_id",
    "source_version_id",
)


class AccessViolationError(RuntimeError):
    """The vector store returned a point outside the caller's access. Never shown to users."""


@dataclass(frozen=True, slots=True)
class SearchHit:
    point_id: str
    score: float
    payload: dict[str, Any]

    @property
    def text(self) -> str:
        return str(self.payload.get("text", ""))


class VectorStore:
    def __init__(self, client: QdrantClient, collection: str, dense_size: int) -> None:
        self._client = client
        self._collection = collection
        self._dense_size = dense_size

    @classmethod
    def from_settings(cls, settings: Settings, dense_size: int) -> VectorStore:
        if not settings.qdrant_url:
            raise ValueError("QDRANT_URL is not configured")
        api_key = settings.qdrant_api_key.get_secret_value() if settings.qdrant_api_key else None
        # No compatibility check at start-up: it would make a network call before the first request.
        client = QdrantClient(url=settings.qdrant_url, api_key=api_key, timeout=30, check_compatibility=False)
        return cls(client, settings.qdrant_collection, dense_size)

    def ensure_collection(self) -> None:
        if not self._client.collection_exists(self._collection):
            self._client.create_collection(
                collection_name=self._collection,
                vectors_config={
                    DENSE: models.VectorParams(size=self._dense_size, distance=models.Distance.COSINE)
                },
                sparse_vectors_config={SPARSE: models.SparseVectorParams(modifier=models.Modifier.IDF)},
            )
        existing = self._client.get_collection(self._collection).payload_schema or {}
        wanted: dict[str, Any] = {
            "tenant_id": models.KeywordIndexParams(type=models.KeywordIndexType.KEYWORD, is_tenant=True),
            "quarantined": models.PayloadSchemaType.BOOL,
            "clearance_rank": models.PayloadSchemaType.INTEGER,
            **{name: models.PayloadSchemaType.KEYWORD for name in _KEYWORD_FIELDS},
        }
        for field_name, schema in wanted.items():
            if field_name not in existing:
                self._client.create_payload_index(
                    self._collection, field_name=field_name, field_schema=schema
                )

    def upsert(
        self,
        ctx: AccessContext,
        chunks: Sequence[Chunk],
        embeddings: Sequence[Embedding],
        *,
        source_id: str,
        source_version_id: str,
        source_file: str,
    ) -> None:
        points = [
            models.PointStruct(
                id=chunk.chunk_id,
                vector={
                    DENSE: embedding.dense,
                    SPARSE: models.SparseVector(
                        indices=embedding.sparse.indices, values=embedding.sparse.values
                    ),
                },
                payload={
                    "tenant_id": ctx.tenant_id,
                    "product_id": ctx.product_id,
                    "module": ctx.module,
                    "entity_id": chunk.entity_id,
                    "employee_id": chunk.employee_id,
                    "chunk_type": chunk.chunk_type,
                    "classification": chunk.classification,
                    "clearance_rank": clearance_rank(chunk.classification),
                    "quarantined": chunk.quarantined,
                    "injection_signals": list(chunk.injection_signals),
                    "source_id": source_id,
                    "source_version_id": source_version_id,
                    "source_file": source_file,
                    "location": chunk.location,
                    "locator": chunk.locator,
                    "record_id": chunk.record_id,
                    "attendance_date": chunk.attendance_date,
                    "heading": chunk.heading,
                    "text": chunk.text,
                },
            )
            for chunk, embedding in zip(chunks, embeddings, strict=True)
        ]
        for start in range(0, len(points), 64):
            self._client.upsert(self._collection, points=points[start : start + 64], wait=True)

    def delete_stale(self, ctx: AccessContext, *, source_id: str, keep_version_id: str) -> None:
        """Remove this source's points from earlier versions."""
        selector = models.Filter(
            must=[*_tenant_conditions(ctx), _match("source_id", source_id)],
            must_not=[_match("source_version_id", keep_version_id)],
        )
        self._client.delete(
            self._collection, points_selector=models.FilterSelector(filter=selector), wait=True
        )

    def search(self, ctx: AccessContext, query: Embedding, *, limit: int) -> list[SearchHit]:
        access = access_filter(ctx)
        response = self._client.query_points(
            self._collection,
            prefetch=[
                models.Prefetch(query=query.dense, using=DENSE, limit=limit, filter=access),
                models.Prefetch(
                    query=models.SparseVector(indices=query.sparse.indices, values=query.sparse.values),
                    using=SPARSE,
                    limit=limit,
                    filter=access,
                ),
            ],
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            query_filter=access,
            limit=limit,
            with_payload=True,
        )
        hits = [
            SearchHit(str(point.id), float(point.score), dict(point.payload or {}))
            for point in response.points
        ]
        for hit in hits:
            if not allowed(ctx, hit.payload):
                logger.error("vector_access_violation", extra={"fields": {"point_id": hit.point_id}})
                raise AccessViolationError("A search result was outside the caller's access.")
        return hits

    def ping(self) -> None:
        self._client.get_collections()


def _match(key: str, value: Any) -> models.FieldCondition:
    return models.FieldCondition(key=key, match=models.MatchValue(value=value))


def _tenant_conditions(ctx: AccessContext) -> list[models.Condition]:
    return [
        _match("tenant_id", ctx.tenant_id),
        _match("product_id", ctx.product_id),
        _match("module", ctx.module),
    ]


def access_filter(ctx: AccessContext) -> models.Filter:
    """What this caller may retrieve: their tenant, product and module; nothing quarantined; nothing
    above their clearance; and, below tenant scope, only their departments or their own remarks."""
    must: list[models.Condition] = [
        *_tenant_conditions(ctx),
        _match("quarantined", False),
        models.FieldCondition(key="clearance_rank", range=models.Range(lte=ctx.clearance)),
    ]
    if ctx.scope is Scope.DEPARTMENT:
        must.append(models.FieldCondition(key="entity_id", match=models.MatchAny(any=list(ctx.entity_scope))))
    elif ctx.scope is Scope.SELF:
        must.append(_match("employee_id", ctx.employee_id))
        must.append(_match("chunk_type", "remark"))
    return models.Filter(must=must)


def allowed(ctx: AccessContext, payload: dict[str, Any]) -> bool:
    """The same rules as access_filter, checked again on each returned payload."""
    if (payload.get("tenant_id"), payload.get("product_id"), payload.get("module")) != (
        ctx.tenant_id,
        ctx.product_id,
        ctx.module,
    ):
        return False
    if payload.get("quarantined") is not False or int(payload.get("clearance_rank", 99)) > ctx.clearance:
        return False
    if ctx.scope is Scope.DEPARTMENT:
        return payload.get("entity_id") in ctx.entity_scope
    if ctx.scope is Scope.SELF:
        return payload.get("employee_id") == ctx.employee_id and payload.get("chunk_type") == "remark"
    return True
