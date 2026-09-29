"""Hybrid document search: dense and BM25 retrieval fused in Qdrant (already filtered by access),
then reranked by a cross-encoder and cut at a minimum score."""

from __future__ import annotations

from dataclasses import dataclass

from attendance_ai.core.access import AccessContext
from attendance_ai.stores.embeddings import Embedder
from attendance_ai.stores.vector_store import VectorStore


@dataclass(frozen=True, slots=True)
class DocumentHit:
    point_id: str
    text: str
    source_file: str
    location: str
    chunk_type: str
    fused_score: float
    rerank_score: float | None
    record_id: str | None


class DocumentSearch:
    def __init__(
        self, vector_store: VectorStore, embedder: Embedder, *, candidates: int, top_k: int, min_score: float
    ) -> None:
        self._store = vector_store
        self._embedder = embedder
        self._candidates = candidates
        self._top_k = top_k
        self._min_score = min_score

    def search(self, ctx: AccessContext, query: str) -> list[DocumentHit]:
        hits = self._store.search(ctx, self._embedder.embed_query(query), limit=self._candidates)
        if not hits:
            return []
        scores = self._embedder.rerank(query, [hit.text for hit in hits])
        ranked: list[DocumentHit] = []
        for index, hit in enumerate(hits):
            rerank = scores[index] if scores else None
            if rerank is not None and rerank < self._min_score:
                continue
            ranked.append(
                DocumentHit(
                    point_id=hit.point_id,
                    text=hit.text,
                    source_file=str(hit.payload.get("source_file", "")),
                    location=str(hit.payload.get("location", "")),
                    chunk_type=str(hit.payload.get("chunk_type", "")),
                    fused_score=hit.score,
                    rerank_score=rerank,
                    record_id=hit.payload.get("record_id"),
                )
            )
        ranked.sort(
            key=lambda h: h.rerank_score if h.rerank_score is not None else h.fused_score, reverse=True
        )
        return ranked[: self._top_k]
