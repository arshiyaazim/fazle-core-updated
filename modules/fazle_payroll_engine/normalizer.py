"""
Fazle Payroll Engine — Phone & text normalization utilities.

All Bangladesh number normalization follows the same pattern as
modules/payment_ingest (strip +880/880 prefix, keep last 11 digits).
"""
from __future__ import annotations

import re
import unicodedata
from typing import Optional

_DIGITS = re.compile(r"\D+")
_BENGALI_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")


def normalize_bd_phone(raw: Optional[str]) -> Optional[str]:
    """
    Normalize any Bangladesh phone number to canonical 01XXXXXXXXX (11 digits).
    Returns None if the input cannot be resolved to a valid BD number.

    Accepted inputs:
      - 017XXXXXXXX                  (already canonical)
      - +88017XXXXXXXX / 88017XXXXXXXX
      - 1712345678                   (10-digit, missing leading zero — common
                                      when typed in WhatsApp without the 0)
    Bengali numerals are converted to ASCII before parsing.
    """
    if not raw:
        return None
    # Convert Bengali/Arabic-Indic numerals to ASCII
    s = str(raw).translate(_BENGALI_DIGITS)
    # Strip all non-digits
    d = _DIGITS.sub("", s)
    if not d:
        return None
    # Strip leading country code (880 / 88)
    if d.startswith("880") and len(d) >= 13:
        d = d[3:]
    elif d.startswith("88") and len(d) >= 12:
        d = d[2:]
    # 10-digit form (e.g. 1725494969) — prepend leading zero
    if len(d) == 10 and d.startswith("1"):
        d = "0" + d
    # Must now be exactly 11 digits starting with 01
    if len(d) != 11 or not d.startswith("01"):
        return None
    return d


def normalize_name(raw: Optional[str]) -> str:
    """
    Normalize a name for comparison:
    - lowercase
    - NFKC unicode normalization
    - strip leading/trailing whitespace
    - collapse internal whitespace
    - remove trailing punctuation like '-', '.'
    """
    if not raw:
        return ""
    s = unicodedata.normalize("NFKC", raw.strip().lower())
    s = re.sub(r"\s+", " ", s)
    s = s.rstrip("-. ")
    return s


_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_PUNCT_TO_SPACE = re.compile(r"[^a-z0-9\s]+")


def normalize_search_text(raw: Optional[str]) -> str:
    """Search-friendly form: lowercase, NFKC, hyphens/punct → space, collapsed.

    Examples:
        "Al-Momin"          → "al momin"
        "  Al  Momin "      → "al momin"
        "Debashish, S.G."   → "debashish s g"
        "ALMOMIN"           → "almomin"
    """
    if not raw:
        return ""
    s = unicodedata.normalize("NFKC", str(raw)).lower()
    s = _PUNCT_TO_SPACE.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def collapse_search_text(raw: Optional[str]) -> str:
    """Collapsed token form (no spaces, no punctuation).

    Examples:
        "Al-Momin"          → "almomin"
        "Al Momin"          → "almomin"
        "Shohel Halisahar"  → "shohelhalisahar"
        "Debashish SG"      → "debashishsg"
    """
    if not raw:
        return ""
    s = unicodedata.normalize("NFKC", str(raw)).lower()
    return _NON_ALNUM.sub("", s)


def normalize_amount(raw: Optional[str]) -> Optional[float]:
    """
    Parse amount strings like "2,200", "2200", "1,530" → float.
    Returns None if unparseable.
    """
    if not raw:
        return None
    s = str(raw).translate(_BENGALI_DIGITS)
    s = s.replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def normalize_payout_method(raw: Optional[str]) -> str:
    """
    Normalize method code to canonical name.
    Handles single-letter codes and full names.
    """
    if not raw:
        return "unknown"
    m = raw.strip().lower()
    mapping = {
        "b": "bkash", "bkash": "bkash",
        "n": "nagad",  "nagad": "nagad",
        "c": "cash",   "cash": "cash",
        "r": "rocket", "rocket": "rocket",
        "bk": "bkash",
    }
    return mapping.get(m, "unknown")


def jid_to_phone(jid: str) -> Optional[str]:
    """
    Extract phone number from WhatsApp JID.
    '8801958122300@s.whatsapp.net' → '01958122300'
    '101481722183752@lid' → None (LID, not directly resolvable)
    """
    if "@s.whatsapp.net" in jid:
        raw = jid.split("@")[0]
        return normalize_bd_phone(raw)
    return None
