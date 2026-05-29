"""Build safe queue items from pending social inbox events."""
from __future__ import annotations

import logging
import os

from app.database import execute, fetch_val

from .classifier import classify_comment, classify_message
from .conversation_history import pending_events, pending_threads
from .employee_lookup import find_by_mobile
from .intelligent_generator import plan_reply
from .risk_flagger import is_recruiting_intent
from .send_queue import enqueue_reply

_COOLDOWN_MINUTES = int(__import__("os").getenv("SOCIAL_REPLY_COOLDOWN_M", "15"))

log = logging.getLogger("fazle.social.planner")


async def process_due_threads(limit: int = 10) -> dict:
    compression_seconds = int(os.getenv("SOCIAL_REPLY_COMPRESSION_S", "25"))
    planned = flagged = skipped = 0
    for thread in await pending_threads(compression_seconds, limit=limit):
        platform = thread["platform"]
        target_id = thread["target_id"]
        events = await pending_events(platform, target_id)
        if not events:
            skipped += 1
            continue
        event_ids = [int(e["id"]) for e in events]
        # PATCH 3: employee role detection before intent gate
        employee = await find_by_mobile(target_id)
        sender_role = "EMPLOYEE" if employee else "UNKNOWN"
        classifications = [_classify_event(e).intent for e in events]
        await _refresh_classifications(events, classifications)
        if not all(is_recruiting_intent(intent) for intent in classifications):
            await execute("UPDATE social_inbox_events SET reply_status='ignored' WHERE id = ANY($1::bigint[])", event_ids)
            skipped += 1
            continue
        try:
            plan = await plan_reply(platform, target_id, events)
        except Exception:
            await execute("UPDATE social_inbox_events SET reply_status='pending' WHERE id = ANY($1::bigint[])", event_ids)
            raise
        if not plan:
            await execute("UPDATE social_inbox_events SET reply_status='ignored' WHERE id = ANY($1::bigint[])", event_ids)
            skipped += 1
            continue
        if not plan.auto_send:
            await execute("UPDATE social_inbox_events SET reply_status='ignored' WHERE id = ANY($1::bigint[])", plan.event_ids)
            skipped += 1
            continue
        # PATCH 2: per-sender intent cooldown — skip duplicate within cooldown window
        recent_dupe = await fetch_val(
            """
            SELECT id FROM social_reply_queue
            WHERE target_id = $1
              AND intent = $2
              AND created_at >= NOW() - ($3 * INTERVAL '1 minute')
            LIMIT 1
            """,
            target_id,
            plan.intent,
            _COOLDOWN_MINUTES,
        )
        if recent_dupe:
            log.info("SKIPPED_DUPLICATE sender=%s intent=%s cooldown=%dm", target_id, plan.intent, _COOLDOWN_MINUTES)
            await execute("UPDATE social_inbox_events SET reply_status='ignored' WHERE id = ANY($1::bigint[])", plan.event_ids)
            skipped += 1
            continue
        queue_id = await enqueue_reply(
            event_id=plan.event_ids[-1],
            platform=platform,
            target_id=target_id,
            reply_text=plan.reply_text,
            intent=plan.intent,
            conversation_id=events[-1].get("conversation_id"),
            reply_to_comment_id=plan.reply_to_comment_id,
            source_bridge=None,
            meta={"combined_event_ids": plan.event_ids, "planner": "context_v1", "sender_role": sender_role},
        )
        if queue_id:
            await execute("UPDATE social_inbox_events SET reply_status='queued' WHERE id = ANY($1::bigint[])", plan.event_ids)
            planned += 1
    return {"planned": planned, "flagged": flagged, "skipped": skipped}


async def _flag_events(event_ids: list[int], platform: str, target_id: str, reason: str, events: list[dict]) -> None:
    text = "\n".join(str(e.get("message_text") or "") for e in events)
    await execute("UPDATE social_inbox_events SET reply_status='flagged' WHERE id = ANY($1::bigint[])", event_ids)
    await execute(
        """
        INSERT INTO social_flagged_items (event_id, platform, target_id, reason, message_text, details)
        VALUES ($1,$2,$3,$4,$5,jsonb_build_object('combined_event_ids', $6::bigint[]))
        """,
        event_ids[-1],
        platform,
        target_id,
        reason,
        text,
        event_ids,
    )


def _classify_event(event: dict):
    text = str(event.get("message_text") or "")
    if event.get("platform") == "facebook_comment":
        return classify_comment(text)
    return classify_message(text, media_flag=bool(event.get("media_flag")), platform=str(event.get("platform") or ""))


async def _refresh_classifications(events: list[dict], classifications: list[str]) -> None:
    for event, intent in zip(events, classifications):
        if event.get("classification") != intent:
            await execute("UPDATE social_inbox_events SET classification=$2 WHERE id=$1", int(event["id"]), intent)
