# =============================================================================
# MODULE STATUS: DORMANT (production) / ACTIVE (tests only)
# Date audited: 2026-06-01
# Production callers: 0 (grep confirmed — no import in app/ or modules/)
# Test references: 4 lines in tests/unit/test_accountant_payment_pipeline.py
#   (patched as mock only — not actually imported or used in the pipeline)
# GAP_SCAN_ENABLED: not applicable to this module
# DO NOT DELETE without explicit confirmation from Azim first.
# =============================================================================
"""
Fazle Core — Short-Term Context Memory (Stage 3)

Lightweight in-process memory: stores the last 3 messages per sender.
No DB, no persistence — resets on service restart. This is intentional:
we only need transient context within a conversation window.

Public API:
    push(sender, text, intent)          → record a message
    get_history(sender)                 → list[dict]  (newest last)
    is_repeated(sender, text)           → bool   (same text ≥2 times in window)
    is_frustrated(sender)               → bool   (3 messages with no reply topic change)
    get_last_intent(sender)             → str | None
    clear(sender)                       → wipe memory for a sender
"""

import time
import hashlib
import logging
from collections import deque
from typing import Optional

log = logging.getLogger("fazle.context_memory")

# Maximum messages stored per sender (rolling window)
_MAX_MESSAGES = 3

# TTL: if last message is older than this, treat as a fresh conversation
_SESSION_TTL = 3600.0  # 1 hour

# in-process store: sender → deque of {text, intent, ts, text_hash}
_STORE: dict[str, deque] = {}


def _evict_stale() -> None:
    """Remove entries whose last message is older than _SESSION_TTL."""
    now = time.time()
    stale = [
        s for s, q in _STORE.items()
        if q and now - q[-1]["ts"] > _SESSION_TTL
    ]
    for s in stale:
        del _STORE[s]


def push(sender: str, text: str, intent: str = "unknown") -> None:
    """Record an inbound message from sender into their context window."""
    _evict_stale()
    if sender not in _STORE:
        _STORE[sender] = deque(maxlen=_MAX_MESSAGES)
    h = hashlib.md5(text.lower().strip().encode()).hexdigest()
    _STORE[sender].append({"text": text[:300], "intent": intent, "ts": time.time(), "h": h})
    log.debug(f"[ctx_mem] {sender} window={len(_STORE[sender])}")


def get_history(sender: str) -> list:
    """Return stored messages for sender, oldest first."""
    q = _STORE.get(sender)
    if not q:
        return []
    return list(q)


def is_repeated(sender: str, text: str) -> bool:
    """Return True if this exact text has appeared ≥2 times in the current window."""
    q = _STORE.get(sender)
    if not q or len(q) < 2:
        return False
    h = hashlib.md5(text.lower().strip().encode()).hexdigest()
    count = sum(1 for m in q if m["h"] == h)
    return count >= 2


def is_frustrated(sender: str) -> bool:
    """
    Return True if the context window suggests frustration:
    - 3 consecutive messages with the same intent (stuck / no resolution)
    - OR the last 2 messages are near-identical (exact repeat)
    """
    q = _STORE.get(sender)
    if not q or len(q) < 2:
        return False
    msgs = list(q)
    # near-identical last 2 messages
    if len(msgs) >= 2 and msgs[-1]["h"] == msgs[-2]["h"]:
        return True
    # 3 messages all same intent (not greeting/unknown)
    if len(msgs) == 3:
        intents = [m["intent"] for m in msgs]
        if intents[0] == intents[1] == intents[2] and intents[0] not in ("greeting", "unknown"):
            return True
    return False


def get_last_intent(sender: str) -> Optional[str]:
    """Return the intent of the most recent stored message, or None."""
    q = _STORE.get(sender)
    if not q:
        return None
    return q[-1]["intent"]


def clear(sender: str) -> None:
    """Wipe context memory for a sender (call after successful resolution)."""
    _STORE.pop(sender, None)
