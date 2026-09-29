"""Detection of instruction-like text in uploaded content and in questions.

Uploaded content is data, never instructions. Detection is one layer: flagged chunks are quarantined
(never used as answer context). Isolation, grounding checks and citation validation still hold for
anything detection misses.
"""

import re

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (name, re.compile(pattern, re.IGNORECASE))
    for name, pattern in (
        (
            "ignore_instructions",
            r"\b(ignore|disregard|forget)\b.{0,40}\b(instructions?|rules|policies|prompts?)\b",
        ),
        ("role_change", r"\byou are now\b|\bact as (an? )?(admin|administrator|system|developer)\b"),
        ("privileged_mode", r"\b(administrator|admin|developer|god|debug|jailbreak)\s+mode\b"),
        ("system_prompt", r"\bsystem (prompt|instructions?|message)\b|\binstructions? for the ai\b"),
        (
            "override_access",
            r"\b(override|bypass|disable)\b.{0,40}\b(access|rules|polic(y|ies)|security|filters?)\b",
        ),
        (
            "exfiltration",
            r"\b(list|show|reveal|dump|export)\b.{0,40}\b(all|every)\b.{0,40}\b(compan(y|ies)|tenants?)\b",
        ),
        ("concealment", r"\bdo not (mention|reveal|disclose)\b.{0,30}\b(this|these|instructions?)\b"),
    )
)


def detect_injection(text: str) -> list[str]:
    """Names of the injection patterns found in ``text`` (empty when it looks like plain data)."""
    return [name for name, pattern in _PATTERNS if pattern.search(text)]
