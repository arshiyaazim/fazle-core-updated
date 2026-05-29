"""Persistence helpers for social auto-reply state and admin status."""
from __future__ import annotations

import json
import os
from typing import Any

from app.database import execute, fetch_all, fetch_one, fetch_val


def daemon_enabled() -> bool:
    return os.getenv("SOCIAL_AUTO_REPLY_ENABLED", "false").lower() in {"1", "true", "yes"}


async def set_paused(paused: bool, reason: str = "manual") -> None:
    await execute(
        """
        INSERT INTO social_backlog_state (state_key, platform, metadata, last_checked_at)
        VALUES ('daemon_paused', 'global', $1::jsonb, NOW())
        ON CONFLICT (state_key) DO UPDATE SET metadata=EXCLUDED.metadata, last_checked_at=NOW()
        """,
        json.dumps({"paused": paused, "reason": reason}),
    )


async def is_paused() -> bool:
    if not daemon_enabled():
        return True
    row = await fetch_one("SELECT metadata FROM social_backlog_state WHERE state_key='daemon_paused'")
    if not row:
        return False
    meta = row.get("metadata") or {}
    if isinstance(meta, str):
        meta = json.loads(meta)
    return bool(meta.get("paused"))


async def status() -> dict[str, Any]:
    pending = await fetch_val("SELECT COUNT(*) FROM social_reply_queue WHERE status='pending'")
    failed = await fetch_val("SELECT COUNT(*) FROM social_reply_queue WHERE status='failed'")
    flagged = await fetch_val("SELECT COUNT(*) FROM social_flagged_items WHERE status='open'")
    sent = await fetch_val("SELECT COUNT(*) FROM social_sent_log WHERE sent_at >= NOW() - INTERVAL '24 hours'")
    rate = await fetch_one("SELECT * FROM social_rate_limit_state WHERE channel='global'")
    return {
        "enabled": daemon_enabled(),
        "paused": await is_paused(),
        "pending": int(pending or 0),
        "failed": int(failed or 0),
        "flagged_open": int(flagged or 0),
        "sent_24h": int(sent or 0),
        "rate_limit": dict(rate) if rate else None,
    }


async def queue_rows(limit: int = 50) -> list[dict]:
    return await fetch_all(
        """
        SELECT id, platform, target_id, intent, status, attempts, next_retry_at, last_error, created_at
        FROM social_reply_queue
        ORDER BY created_at DESC
        LIMIT $1
        """,
        limit,
    )


async def flagged_rows(limit: int = 50) -> list[dict]:
    return await fetch_all(
        """
        SELECT id, platform, target_id, reason, severity, message_text, status, created_at
        FROM social_flagged_items
        ORDER BY created_at DESC
        LIMIT $1
        """,
        limit,
    )
