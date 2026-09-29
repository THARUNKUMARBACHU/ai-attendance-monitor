"""Index a source's chunks in the vector store: embed locally, upsert, drop earlier versions."""

from __future__ import annotations

import threading
from collections.abc import Sequence
from dataclasses import dataclass

from attendance_ai.core.access import AccessContext
from attendance_ai.stores.embeddings import Embedder
from attendance_ai.stores.vector_store import Chunk, VectorStore


@dataclass(frozen=True, slots=True)
class IndexOutcome:
    indexed: int
    quarantined: int


class DocumentIndexer:
    def __init__(self, vector_store: VectorStore, embedder: Embedder) -> None:
        self._store = vector_store
        self._embedder = embedder
        self._ready = False
        self._lock = threading.Lock()

    def index_source(
        self,
        ctx: AccessContext,
        chunks: Sequence[Chunk],
        *,
        source_id: str,
        source_version_id: str,
        source_file: str,
    ) -> IndexOutcome:
        self._ensure_ready()
        if chunks:
            embeddings = self._embedder.embed_documents([chunk.text for chunk in chunks])
            self._store.upsert(
                ctx,
                chunks,
                embeddings,
                source_id=source_id,
                source_version_id=source_version_id,
                source_file=source_file,
            )
        self._store.delete_stale(ctx, source_id=source_id, keep_version_id=source_version_id)
        return IndexOutcome(
            indexed=sum(1 for chunk in chunks if not chunk.quarantined),
            quarantined=sum(1 for chunk in chunks if chunk.quarantined),
        )

    def _ensure_ready(self) -> None:
        if self._ready:
            return
        with self._lock:
            if not self._ready:
                self._store.ensure_collection()
                self._ready = True
