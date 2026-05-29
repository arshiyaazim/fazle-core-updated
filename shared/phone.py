"""
Fazle Core — Shared Phone Normalization
========================================

A thin, stable re-export layer so all modules can import from one
consistent location without depending on each other directly.

USAGE
-----
    from shared.phone import normalize_phone, phone_last10, phones_match
    from shared.phone import extract_phone_candidates

DO NOT add business logic here — keep it as a pure passthrough.
"""

from __future__ import annotations

import re
from typing import Optional

# Re-exports from the authoritative sources.
# shared.phone has NO logic of its own; all implementation lives in the
# modules it wraps so those modules can keep their own normalisation rules.

from modules.number_identity import (
    canonical_phone,
    phone_last10,
)
from modules.fazle_payroll_engine.normalizer import (
    normalize_bd_phone,
)

# ── Phone extraction helpers ──────────────────────────────────────────────────

# Strip spaces/hyphens that appear BETWEEN digits so:
#   "+880 1849-258074" → "+8801849258074"
#   "01849-258074"     → "01849258074"
_RE_INTERDIGIT_NOISE = re.compile(r"(?<=\d)[\s\-](?=\d)")

# Loose BD phone pattern applied AFTER inter-digit stripping
_RE_BD_PHONE_LOOSE = re.compile(r"(?:\+?880)?0[1-9]\d{9}", re.ASCII)


def extract_phone_candidates(text: Optional[str]) -> list[str]:
    """
    Extract all Bangladesh phone candidates from free-form text.

    Handles formatted variants that the strict `_RE_PHONE` regex cannot:
      +880 1849-258074   (space after country code, hyphen in number)
      01849-258074       (hyphen only)
      01849 258074       (space only)
      +8801849258074     (no separators — already works)

    Returns a deduplicated list of canonical 01XXXXXXXXX phones in order
    of first appearance.  Returns [] when text is None or empty.
    """
    if not text:
        return []
    # Collapse inter-digit noise so all BD formats become continuous digits
    normalized = _RE_INTERDIGIT_NOISE.sub("", str(text))
    raw_phones = _RE_BD_PHONE_LOOSE.findall(normalized)
    result: list[str] = []
    for ph in raw_phones:
        canon = normalize_bd_phone(ph)
        if canon and canon not in result:
            result.append(canon)
    return result


def normalize_phone(raw: Optional[str]) -> str:
    """
    Canonical Bangladesh phone number → 01XXXXXXXXX (11 digits).
    Returns empty string for unresolvable inputs.

    Thin alias for `canonical_phone()` from modules.number_identity.
    """
    return canonical_phone(raw or "") if raw else ""


def phones_match(a: Optional[str], b: Optional[str]) -> bool:
    """
    Return True if two phone strings resolve to the same number.
    Matching is done on last-10 digits, tolerating prefix differences.

    Examples
    --------
    >>> phones_match("+8801712345678", "01712345678")
    True
    >>> phones_match("01712345678", "01987654321")
    False
    >>> phones_match(None, "017...")
    False
    """
    if not a or not b:
        return False
    tail_a = phone_last10(a)
    tail_b = phone_last10(b)
    return bool(tail_a and tail_b and tail_a == tail_b)


# Make all names importable directly from shared.phone
__all__ = [
    "canonical_phone",
    "phone_last10",
    "normalize_bd_phone",
    "normalize_phone",
    "phones_match",
    "extract_phone_candidates",
]
