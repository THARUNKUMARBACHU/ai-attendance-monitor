"""Approved feedback examples, and finding the ones that apply to a question.

An example applies only to callers with exactly the scope it was approved for: the same tenant, product
and module (row-level security), the same scope type, the same departments or the same employee, and at
least its clearance. It also applies only to an equivalent question: cosine similarity of the local
embeddings at or above the threshold or, when no embedding model is configured, a near-identical wording.
"""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from attendance_ai.core.access import AccessContext, Scope
from attendance_ai.stores.db import Database
from attendance_ai.stores.embeddings import Embedder
from attendance_ai.stores.models import FeedbackExample

logger = logging.getLogger(__name__)

MAX_EXAMPLES = 2
LEXICAL_MIN_SIMILARITY = 0.8  # without embeddings, only near-identical questions match
_STOPWORDS = frozenset(
    {
        *("a", "an", "and", "are", "as", "at", "be", "by", "did", "do", "does", "for", "from", "has"),
        *("have", "how", "i", "in", "is", "it", "me", "my", "of", "on", "or", "please", "show", "tell"),
        *("the", "to", "was", "were", "what", "when", "which", "who", "why", "with"),
    }
)


@dataclass(frozen=True, slots=True)
class Example:
    example_id: str
    question: str
    ideal_output: str
    version: int
    similarity: float


class ExampleStore:
    def __init__(self, database: Database, embedder: Embedder | None, *, min_similarity: float) -> None:
        self._db = database
        self._embedder = embedder
        self._min_similarity = min_similarity

    def embed(self, text: str) -> list[float] | None:
        """The question's dense embedding, stored with a new example (None without a model)."""
        if self._embedder is None:
            return None
        try:
            return self._embedder.embed_query(text).dense
        except Exception:  # the model is optional: matching falls back to wording
            logger.exception("feedback_embedding_failed")
            return None

    def find(self, ctx: AccessContext, question: str) -> list[Example]:
        """The active examples (at most two, best first) that apply to this caller and question."""
        try:
            with self._db.session_for(ctx) as session:
                rows = session.scalars(
                    select(FeedbackExample).where(
                        FeedbackExample.status == "active", FeedbackExample.scope == ctx.scope.value
                    )
                ).all()
                candidates = [
                    (str(row.id), row.question, row.ideal_output, row.version, row.question_embedding)
                    for row in rows
                    if applies_to(row, ctx)
                ]
        except SQLAlchemyError:
            logger.exception("feedback_lookup_failed")
            return []
        if not candidates:
            return []
        vector = self.embed(question) if any(embedding for *_, embedding in candidates) else None
        matches: list[Example] = []
        for example_id, text, ideal_output, version, embedding in candidates:
            if normalise(text) == normalise(question):
                score, threshold = 1.0, 1.0
            elif vector is not None and embedding:
                score, threshold = cosine(vector, embedding), self._min_similarity
            else:
                score, threshold = word_overlap(question, text), LEXICAL_MIN_SIMILARITY
            if score >= threshold:
                matches.append(Example(example_id, text, ideal_output, version, round(score, 3)))
        matches.sort(key=lambda example: (-example.similarity, -example.version))
        return matches[:MAX_EXAMPLES]


def applies_to(row: FeedbackExample, ctx: AccessContext) -> bool:
    """Exactly the scope the example was approved for (row-level security already limits the tenant,
    product and module, and never shows an example above the caller's clearance)."""
    if row.scope != ctx.scope.value or row.clearance > ctx.clearance:
        return False
    if ctx.scope is Scope.DEPARTMENT:
        return sorted(row.entity_scope or []) == sorted(ctx.entity_scope)
    if ctx.scope is Scope.SELF:
        return row.employee_id == ctx.employee_id
    return True


def normalise(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


def word_overlap(first: str, second: str) -> float:
    """Jaccard similarity of the content words of two questions."""
    a, b = _content_words(first), _content_words(second)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def cosine(first: Sequence[float], second: Sequence[float]) -> float:
    if len(first) != len(second):
        return 0.0
    dot = sum(x * y for x, y in zip(first, second, strict=True))
    norm = math.sqrt(sum(x * x for x in first)) * math.sqrt(sum(y * y for y in second))
    return dot / norm if norm else 0.0


def _content_words(text: str) -> set[str]:
    return {word for word in re.findall(r"[a-z0-9]+", text.lower()) if word not in _STOPWORDS}
