"""Checks on a reviewer's feedback before it can become an approved example.

Feedback is untrusted input. It may change how answers are phrased and what they include; it can never
change facts or access rules. So the text is checked for instructions, contact details and anything the
example's scope may not see, and the example is then replayed: the original question is answered again
with the example applied, and the example is activated only if that answer carries the ideal output's
numbers and follows it closely.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from attendance_ai.core.access import AccessContext, Scope, clearance_rank
from attendance_ai.core.directory import Directory
from attendance_ai.governance.grounding import number_supported, stated_numbers
from attendance_ai.governance.injection import detect_injection
from attendance_ai.governance.pii import classify_text
from attendance_ai.orchestration.guard import (
    foreign_names,
    mentioned_entities,
    mentions_name,
    names_other_tenant,
)
from attendance_ai.orchestration.models import QueryResponse

REPLAY_MIN_SIMILARITY = 0.5
_CITATION = re.compile(r"\[[DS]\d+\]")
_TOKEN = re.compile(r"\d+(?:\.\d+)?%?|[a-z]+")  # words, and numbers with their decimals and percent sign


def policy_problems(
    directory: Directory, origin: AccessContext, *, feedback: str, ideal_output: str
) -> list[str]:
    """Why this feedback cannot be accepted for the example's scope (empty when it can)."""
    problems: list[str] = []
    for label, text in (("feedback", feedback), ("ideal output", ideal_output)):
        signals = detect_injection(text)
        if signals:
            problems.append(f"The {label} contains instruction-like text ({', '.join(signals)}).")

    sensitivity, kinds = classify_text(ideal_output)
    contact = [kind for kind in kinds if kind in ("phone", "email")]
    if contact:
        problems.append(f"The ideal output contains contact details ({', '.join(contact)}).")
    elif sensitivity == "restricted" and origin.clearance < clearance_rank("restricted"):
        problems.append("The ideal output contains restricted details that this scope may not see.")

    if names_other_tenant(directory, origin.tenant_id, ideal_output) or any(
        mentions_name(ideal_output, name) for name in foreign_names(directory, origin.tenant_id)
    ):
        problems.append("The ideal output names another organisation or its people.")

    tenant = directory.get_tenant(origin.tenant_id)
    if tenant is not None and origin.scope is not Scope.TENANT:
        departments, employees = mentioned_entities(tenant, ideal_output)
        if origin.scope is Scope.DEPARTMENT:
            visible = set(origin.entity_scope)
        else:
            own = tenant.employees.get(origin.employee_id or "")
            visible = {own.entity_id} if own else set()
            employees = [e for e in employees if e.employee_id != origin.employee_id]
        outside = [d.name for d in departments if d.entity_id not in visible] + [
            e.name for e in employees if origin.scope is Scope.SELF or e.entity_id not in visible
        ]
        if outside:
            problems.append(
                "The ideal output mentions data outside the example's scope: "
                + ", ".join(sorted(set(outside)))
            )
    return problems


def replay_problems(
    response: QueryResponse, ideal_output: str, question: str
) -> tuple[list[str], float, list[str]]:
    """Problems with the replayed answer, its similarity to the ideal output, and any of the ideal
    output's numbers that the replayed (grounded) answer does not carry."""
    problems: list[str] = []
    answer = response.answer or ""
    if response.outcome != "answered":
        problems.append(
            f"The replayed question did not produce a normal answer ({response.outcome}"
            + (f": {response.reason_code}" if response.reason_code else "")
            + ")."
        )
    carried = [*stated_numbers(answer), *stated_numbers(question)]
    missing = [token for token in stated_numbers(ideal_output) if not number_supported(token, carried)]
    if missing:
        problems.append(
            "The data does not support these numbers from the ideal output: " + ", ".join(missing) + "."
        )
    similarity = text_similarity(answer, ideal_output)
    if answer and similarity < REPLAY_MIN_SIMILARITY:
        problems.append(
            "The replayed answer does not follow the ideal output closely enough "
            f"(similarity {similarity:.2f})."
        )
    return problems, similarity, missing


def text_similarity(first: str, second: str) -> float:
    """Word-sequence similarity (0..1), ignoring citation markers and case."""
    a = _TOKEN.findall(_CITATION.sub("", first).lower())
    b = _TOKEN.findall(_CITATION.sub("", second).lower())
    if not a or not b:
        return 0.0
    return round(SequenceMatcher(None, a, b, autojunk=False).ratio(), 3)
