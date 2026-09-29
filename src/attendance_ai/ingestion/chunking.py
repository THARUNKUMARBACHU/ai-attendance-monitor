"""Split what is worth searching into chunks: each record's remark, and narrative text from documents.

Every chunk carries its isolation context (department or tenant-wide, employee, classification) so
the vector store can filter before retrieval. Chunks that look like instructions are quarantined.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Sequence

from attendance_ai.governance.injection import detect_injection
from attendance_ai.governance.pii import classify_text
from attendance_ai.ingestion.types import ExtractedText
from attendance_ai.stores.models import AttendanceRecord
from attendance_ai.stores.vector_store import TENANT_WIDE, Chunk

MAX_CHUNK_WORDS = 220
_NAMESPACE = uuid.UUID("6f1d2c3e-8b7a-4c5d-9e0f-1a2b3c4d5e6f")


def _chunk_id(*parts: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, "|".join(parts)))


def _digest(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8"), usedforsecurity=False).hexdigest()[:12]


def remark_chunks(source_id: str, records: Sequence[AttendanceRecord]) -> list[Chunk]:
    chunks: list[Chunk] = []
    for record in records:
        if not record.remarks:
            continue
        status = record.status.replace("_", " ").title() if record.status else "Status unreadable"
        text = (
            f"{record.employee_name} ({record.employee_id}), {record.department}, "
            f"{record.attendance_date:%d %B %Y}: {status}. Remark: {record.remarks}"
        )
        signals = tuple(detect_injection(record.remarks))
        restricted = record.remarks_sensitivity == "restricted"
        chunks.append(
            Chunk(
                chunk_id=_chunk_id(source_id, "remark", record.record_key, _digest(text)),
                text=text,
                chunk_type="remark",
                entity_id=record.entity_id,
                classification="restricted" if restricted else record.classification,
                location=record.source_page_or_row,
                locator=record.source_locator,
                employee_id=record.employee_id,
                attendance_date=record.attendance_date.isoformat(),
                record_id=str(record.id),
                quarantined=bool(signals),
                injection_signals=signals,
            )
        )
    return chunks


def narrative_chunks(
    source_id: str, texts: Sequence[ExtractedText], *, entity_id: str | None, classification: str
) -> list[Chunk]:
    """Group consecutive blocks under the same heading into chunks of up to MAX_CHUNK_WORDS words."""
    chunks: list[Chunk] = []
    group: list[ExtractedText] = []

    def flush() -> None:
        if not group:
            return
        heading = group[0].heading
        body = "\n".join(block.text.strip() for block in group if block.text.strip())
        text = f"{heading}\n{body}" if heading else body
        sensitivity, _ = classify_text(body)
        signals = tuple(detect_injection(text))
        chunks.append(
            Chunk(
                chunk_id=_chunk_id(source_id, "narrative", str(len(chunks)), _digest(text)),
                text=text,
                chunk_type="narrative",
                entity_id=entity_id or TENANT_WIDE,
                classification="restricted" if sensitivity == "restricted" else classification,
                location=_describe_span(group),
                locator=group[0].locator.as_dict(),
                heading=heading,
                quarantined=bool(signals),
                injection_signals=signals,
            )
        )
        group.clear()

    words = 0
    for block in texts:
        if not block.text.strip():
            continue
        block_words = len(block.text.split())
        if group and (block.heading != group[0].heading or words + block_words > MAX_CHUNK_WORDS):
            flush()
            words = 0
        group.append(block)
        words += block_words
    flush()
    return chunks


def _describe_span(blocks: Sequence[ExtractedText]) -> str:
    first, last = blocks[0].locator, blocks[-1].locator
    if first.type == "paragraph" and last.type == "paragraph" and first.paragraph != last.paragraph:
        return f"paragraphs {first.paragraph}-{last.paragraph}"
    if first.type == "page_text" and last.type == "page_text" and first.page != last.page:
        return f"pages {first.page}-{last.page}"
    return first.describe()
