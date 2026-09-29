"""A deterministic, explainable confidence score for an answer (0 to 1) with the reasons behind it.

Bands: high >= 0.80, medium >= 0.50, low below. A low-confidence answer is returned as needs_review.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from attendance_ai.retrieval.documents import DocumentHit

Band = Literal["high", "medium", "low"]


@dataclass(frozen=True, slots=True)
class Confidence:
    score: float
    band: Band
    reasons: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ConfidenceInputs:
    mode: str
    has_rows: bool
    evidence: Sequence[dict[str, Any]]
    evidence_available: bool
    documents: Sequence[DocumentHit]
    pending_review: int
    used_fallback_model: bool
    repaired: bool
    unresolved_issues: Sequence[str]
    template_answer: bool


def score_confidence(inputs: ConfidenceInputs) -> Confidence:
    reasons: list[str] = []
    parts: list[float] = []

    if inputs.mode in ("structured", "hybrid") and inputs.has_rows:
        structured = 1.0
        ocr = [row for row in inputs.evidence if row.get("extraction_method") == "ocr"]
        if ocr:
            share = len(ocr) / len(inputs.evidence)
            average = sum(float(row.get("extraction_confidence") or 0) for row in ocr) / len(ocr)
            structured -= share * (0.05 + max(0.0, 1.0 - average))
            reasons.append(f"{len(ocr)} of {len(inputs.evidence)} source records were read by OCR")
        if not inputs.evidence_available:
            structured -= 0.1
            reasons.append("record-level citations could not be derived for this query")
        parts.append(structured)

    if inputs.mode in ("document", "hybrid") and inputs.documents:
        top = inputs.documents[0].rerank_score
        document = 1 / (1 + math.exp(-top)) if top is not None else 0.7
        if document < 0.5:
            reasons.append("the matching documents are only a weak match for the question")
        parts.append(max(0.3, document))

    score = sum(parts) / len(parts) if parts else 0.0
    if inputs.pending_review:
        score -= 0.1
        reasons.append(f"{inputs.pending_review} records in scope are waiting for review and were excluded")
    if inputs.used_fallback_model:
        score -= 0.05
        reasons.append("the fallback model was used")
    if inputs.repaired:
        score -= 0.1
        reasons.append("the first draft failed validation and was corrected")
    if inputs.template_answer:
        score = min(score, 0.6)
        reasons.append("the answer was built directly from the data, without a written summary")
    if inputs.unresolved_issues:
        score = min(score, 0.4)
        reasons.extend(inputs.unresolved_issues)

    score = round(max(0.0, min(1.0, score)), 2)
    band: Band = "high" if score >= 0.8 else "medium" if score >= 0.5 else "low"
    return Confidence(score=score, band=band, reasons=reasons)
