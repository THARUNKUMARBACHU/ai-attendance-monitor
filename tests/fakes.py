"""Deterministic stand-ins for the embedding models: bag-of-words vectors, no downloads."""

import hashlib
import re
from collections.abc import Sequence

from attendance_ai.stores.embeddings import Embedding, SparseVector

DIMENSIONS = 384


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _index(token: str) -> int:
    return int(hashlib.md5(token.encode(), usedforsecurity=False).hexdigest()[:8], 16)


class FakeEmbedder:
    dense_size = DIMENSIONS
    can_rerank = True

    def embed_documents(self, texts: Sequence[str]) -> list[Embedding]:
        return [self.embed_query(text) for text in texts]

    def embed_query(self, text: str) -> Embedding:
        dense = [0.0] * DIMENSIONS
        counts: dict[int, float] = {}
        for token in _tokens(text):
            index = _index(token)
            dense[index % DIMENSIONS] += 1.0
            counts[index % 100_000] = counts.get(index % 100_000, 0.0) + 1.0
        norm = sum(value * value for value in dense) ** 0.5 or 1.0
        return Embedding(
            dense=[value / norm for value in dense],
            sparse=SparseVector(indices=list(counts), values=list(counts.values())),
        )

    def rerank(self, query: str, documents: Sequence[str]) -> list[float]:
        wanted = set(_tokens(query))
        return [float(len(wanted & set(_tokens(document)))) for document in documents]

    def warm_up(self) -> None:
        pass
