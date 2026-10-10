"""Heuristic sensitivity detection; these patterns are not complete DLP."""

import re

from app.schemas import Sensitivity


_EMAIL = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
_PHONE = re.compile(r"(?<!\w)(?:\+?\d[\d .()/-]{7,}\d)(?!\w)")
_AADHAAR = re.compile(r"(?<!\d)\d{4}[ -]?\d{4}[ -]?\d{4}(?!\d)")
_PAN = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b", re.IGNORECASE)
_CARD_CANDIDATE = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")
_API_KEY = re.compile(
    r"\b(?:api[_-]?key|access[_-]?token|secret[_-]?key)\s*[:=]\s*['\"]?[^\s'\"]{8,}"
    r"|\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|AIza[A-Za-z0-9_-]{30,})\b"
    r"|\bBearer\s+[A-Za-z0-9._~-]{16,}",
    re.IGNORECASE,
)
_ORDER = {
    Sensitivity.PUBLIC: 0,
    Sensitivity.INTERNAL: 1,
    Sensitivity.CONFIDENTIAL: 2,
    Sensitivity.RESTRICTED: 3,
}


def _passes_luhn(value: str) -> bool:
    digits = [int(character) for character in value if character.isdigit()]
    if not 13 <= len(digits) <= 19:
        return False
    checksum = 0
    parity = len(digits) % 2
    for index, digit in enumerate(digits):
        if index % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        checksum += digit
    return checksum % 10 == 0


def detect_sensitivity(text: str) -> Sensitivity:
    """Return the strictest level indicated by common sensitive patterns."""
    if any(pattern.search(text) for pattern in (_AADHAAR, _PAN, _API_KEY)):
        return Sensitivity.RESTRICTED
    if any(_passes_luhn(match.group()) for match in _CARD_CANDIDATE.finditer(text)):
        return Sensitivity.RESTRICTED
    if _EMAIL.search(text) or _PHONE.search(text):
        return Sensitivity.INTERNAL
    return Sensitivity.PUBLIC


def effective_sensitivity(declared: Sensitivity, detected: Sensitivity) -> Sensitivity:
    """Choose the stricter of the caller declaration and server heuristic."""
    return max((declared, detected), key=_ORDER.__getitem__)
