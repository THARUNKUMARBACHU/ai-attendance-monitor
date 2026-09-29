"""Local embedding and reranking models (FastEmbed, ONNX on CPU): no API calls, no per-token cost.
Models download once into the model cache directory and load lazily on first use."""

from __future__ import annotations

import os
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from attendance_ai.core.config import Settings


@dataclass(frozen=True, slots=True)
class SparseVector:
    indices: list[int]
    values: list[float]


@dataclass(frozen=True, slots=True)
class Embedding:
    dense: list[float]
    sparse: SparseVector


class Embedder:
    def __init__(
        self, dense_model: str, sparse_model: str, rerank_model: str | None, cache_dir: Path
    ) -> None:
        self._names = (dense_model, sparse_model, rerank_model)
        self._cache_dir = str(cache_dir)
        self._lock = threading.Lock()
        self._dense: Any = None
        self._sparse: Any = None
        self._reranker: Any = None

    @classmethod
    def from_settings(cls, settings: Settings) -> Embedder:
        return cls(
            settings.embedding_model, settings.sparse_model, settings.rerank_model, settings.model_cache_dir
        )

    @property
    def dense_size(self) -> int:
        return 384 if "small" in self._names[0] else 768

    @property
    def can_rerank(self) -> bool:
        return self._names[2] is not None

    def embed_documents(self, texts: Sequence[str]) -> list[Embedding]:
        if not texts:
            return []
        self._load()
        dense = list(self._dense.embed(list(texts)))
        sparse = list(self._sparse.embed(list(texts)))
        return [Embedding(_floats(d), _sparse(s)) for d, s in zip(dense, sparse, strict=True)]

    def embed_query(self, text: str) -> Embedding:
        self._load()
        dense = next(iter(self._dense.query_embed(text)))
        sparse = next(iter(self._sparse.query_embed(text)))
        return Embedding(_floats(dense), _sparse(sparse))

    def rerank(self, query: str, documents: Sequence[str]) -> list[float]:
        if not documents or not self.can_rerank:
            return []
        self._load()
        return [float(score) for score in self._reranker.rerank(query, list(documents))]

    def warm_up(self) -> None:
        self._load()

    def _load(self) -> None:
        if self._dense is not None:
            return
        with self._lock:
            if self._dense is not None:
                return
            # Windows without developer mode cannot symlink; the cache still works, so skip the warning.
            os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
            from fastembed import SparseTextEmbedding, TextEmbedding
            from fastembed.rerank.cross_encoder import TextCrossEncoder

            dense_name, sparse_name, rerank_name = self._names
            self._sparse = SparseTextEmbedding(model_name=sparse_name, cache_dir=self._cache_dir)
            if rerank_name:
                self._reranker = TextCrossEncoder(model_name=rerank_name, cache_dir=self._cache_dir)
            self._dense = TextEmbedding(model_name=dense_name, cache_dir=self._cache_dir)


def _floats(vector: Any) -> list[float]:
    return [float(x) for x in vector]


def _sparse(embedding: Any) -> SparseVector:
    return SparseVector(
        indices=[int(i) for i in embedding.indices], values=[float(v) for v in embedding.values]
    )
