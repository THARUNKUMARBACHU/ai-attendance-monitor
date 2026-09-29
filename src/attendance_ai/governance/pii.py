"""Personal-data detection and masking for free text (remarks, document snippets, answers)."""

import re
from typing import Literal

Sensitivity = Literal["confidential", "restricted"]

_MEDICAL = re.compile(
    r"\b(sick|medical|medic\w*|doctor|hospital\w*|fever|ill(ness)?|clinic|surgery|injur\w*|"
    r"dengue|covid|health|diagnos\w*|prescription|maternity|pregnan\w*)\b",
    re.IGNORECASE,
)
# Ten to fourteen digits, optionally with a country code and separators; dates and times do not match.
_PHONE = re.compile(r"(?<![\w-])(?:\+\d{1,3}[\s-]?)?(?:\d[\s-]?){9,13}\d(?![\w-])")
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")


def classify_text(text: str | None) -> tuple[Sensitivity | None, list[str]]:
    """Sensitivity of a remark and the kinds of personal data in it. Medical details, phone numbers
    and emails make it restricted; any other remark is confidential."""
    if not text or not text.strip():
        return None, []
    kinds = [
        kind
        for kind, pattern in (("medical", _MEDICAL), ("phone", _PHONE), ("email", _EMAIL))
        if pattern.search(text)
    ]
    return ("restricted" if kinds else "confidential"), kinds


def mask_contact_details(text: str) -> str:
    """Replace phone numbers and emails, which answers never need."""
    return _EMAIL.sub("[email]", _PHONE.sub("[phone]", text))
