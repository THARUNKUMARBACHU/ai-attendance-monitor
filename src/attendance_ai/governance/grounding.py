"""Check a drafted answer against the evidence it was given:

- every citation ID exists in the context (unknown ones are removed and reported);
- every number appears in the data or the question (rounding to fewer decimals is allowed);
- no person, department or company is named unless it appears in the context;
- phone numbers and emails are masked.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from attendance_ai.governance.pii import mask_contact_details
from attendance_ai.retrieval.context import PackedContext

_CITATION = re.compile(r"\[([DS]\d+)\]")
_NUMBER = re.compile(r"(?<![\w.])\d+(?:\.\d+)?(?![\w])")
# Small whole numbers appear naturally (list numbering, "two of them"); they are not treated as facts.
_FREE_INTEGERS = frozenset(str(n) for n in range(11))


@dataclass(frozen=True, slots=True)
class GroundingReport:
    answer: str
    citations: list[str]
    issues: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues


def check_answer(
    answer: str,
    cited: Iterable[str],
    *,
    context: PackedContext,
    question: str,
    known_names: Iterable[str],
    foreign_names: Iterable[str],
) -> GroundingReport:
    issues: list[str] = []
    allowed_ids = context.allowed_ids

    used = [cid for cid in dict.fromkeys([*_CITATION.findall(answer), *cited])]
    unknown = [cid for cid in used if cid not in allowed_ids]
    for cid in unknown:
        issues.append(f"cites {cid}, which is not in the context")
        answer = answer.replace(f"[{cid}]", "")
    citations = [cid for cid in used if cid in allowed_ids]

    allowed_numbers = _decimals([*context.numbers, *_NUMBER.findall(question)])
    for token in _NUMBER.findall(_CITATION.sub("", answer)):
        if token not in _FREE_INTEGERS and not _supported(token, allowed_numbers):
            issues.append(f"states the number {token}, which is not in the data")

    context_text = context.text.casefold()
    for name in {*known_names, *foreign_names}:
        if _mentions(answer, name) and name.casefold() not in context_text:
            issues.append(f"mentions {name}, who or which is not in the retrieved data")

    return GroundingReport(answer=mask_contact_details(answer).strip(), citations=citations, issues=issues)


def stated_numbers(text: str) -> list[str]:
    """The numbers a text states as facts: citation markers and small whole numbers are ignored."""
    return [token for token in _NUMBER.findall(_CITATION.sub("", text)) if token not in _FREE_INTEGERS]


def number_supported(token: str, candidates: Iterable[str]) -> bool:
    """Whether ``token`` equals one of ``candidates``, or is one of them rounded to fewer decimals."""
    return _supported(token, _decimals(candidates))


def _decimals(tokens: Iterable[str]) -> list[Decimal]:
    values: list[Decimal] = []
    for token in tokens:
        try:
            values.append(Decimal(token))
        except InvalidOperation:
            continue
    return values


def _supported(token: str, allowed: list[Decimal]) -> bool:
    value = Decimal(token)
    exponent = value.as_tuple().exponent
    places = -exponent if isinstance(exponent, int) else 0
    quantum = Decimal(1).scaleb(-max(places, 0))
    return any(
        value == candidate or candidate.quantize(quantum, ROUND_HALF_UP) == value for candidate in allowed
    )


def _mentions(text: str, name: str) -> bool:
    return bool(name) and re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text, re.IGNORECASE) is not None
