"""Fact anchor extraction and voice output validation."""
from __future__ import annotations

import re

# Numeric codes (3+ digits), percentages, money-like amounts, age limits
_CODE_RE = re.compile(r"\b\d{3,}(?:\+\d{3,})?\b")
_PERCENT_RE = re.compile(r"\b\d+(?:[.,]\d+)?\s*%")
_AMOUNT_RE = re.compile(
    r"\b\d+(?:[.,]\d+)?\s*(?:руб(?:\.|лей)?|₽|тыс\.?|млн\.?)\b",
    re.IGNORECASE,
)
_AGE_LIMIT_RE = re.compile(r"\bдо\s+\d+\s+(?:лет|года|год)\b", re.IGNORECASE)


def extract_anchors(text: str) -> set[str]:
    """Extract fact-like tokens from text for comparison."""
    if not text:
        return set()
    anchors: set[str] = set()
    for pattern in (_CODE_RE, _PERCENT_RE, _AMOUNT_RE, _AGE_LIMIT_RE):
        anchors.update(match.group(0).strip() for match in pattern.finditer(text))
    return anchors


def validate_voice(draft: str, voiced: str) -> str:
    """
    Return voiced text if no new fact anchors were introduced; else draft.
    """
    draft_text = (draft or "").strip()
    voiced_text = (voiced or "").strip()
    if not voiced_text:
        return draft_text

    draft_anchors = extract_anchors(draft_text)
    voiced_anchors = extract_anchors(voiced_text)
    new_anchors = voiced_anchors - draft_anchors
    if new_anchors:
        return draft_text
    return voiced_text
