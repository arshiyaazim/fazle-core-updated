"""
Fazle Core — Persistent Outbound Queue (Batch 15.1 + 15.2)

Tables: fazle_outbound_queue
- enqueue() with idempotency_key dedup
- sweep_once() polls due rows, sends via bridge, retries with exp backoff, → DLQ at max_attempts
- start_background_worker() lifespan task
- Circuit breaker integration: opening enqueues a one-per-minute alert to admin
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Optional

from app.database import execute, fetch_one, fetch_all, fetch_val, get_pool
from app.error_log import record_error

log = logging.getLogger("fazle.outbound")

_worker_task: Optional[asyncio.Task] = None


def _outbound_enabled() -> bool:
    return os.getenv("OUTBOUND_ENABLED", "false").lower() in ("1", "true", "yes")


def _admin_number() -> Optional[str]:
    raw = os.getenv("ADMIN_NUMBERS", "").strip()
    if not raw:
        return None
    return raw.split(",")[0].strip() or None


async def enqueue(
    recipient: str,
    body: str,
    *,
    source_bridge: str = "bridge2",
    purpose: Optional[str] = None,
    idempotency_key: Optional[str] = None,
    meta: Optional[dict[str, Any]] = None,
) -> Optional[int]:
    """Insert into queue. Returns id, or None if dedup'd by idempotency_key."""
    import json
    meta_json = json.dumps(meta or {})
    row = await fetch_one(
        """INSERT INTO fazle_outbound_queue
              (recipient, body, source_bridge, purpose, idempotency_key, meta_json)
           VALUES ($1, $2, $3, $4, $5, $6::jsonb)
           ON CONFLICT (idempotency_key) DO NOTHING
           RETURNING id""",
        recipient, body, source_bridge, purpose, idempotency_key, meta_json,
    )
    qid = row["id"] if row else None
    if qid:
        log.info(f"[OUTBOUND_ENQUEUE] id={qid} recipient={recipient} bridge={source_bridge} purpose={purpose} key={idempotency_key} body={body[:60]!r}")
    else:
        log.info(f"[OUTBOUND_ENQUEUE] dedup_skip recipient={recipient} key={idempotency_key}")
    return qid


async def pending_count() -> int:
    return int(await fetch_val(
        "SELECT COUNT(*) FROM fazle_outbound_queue WHERE status IN ('pending','failed')"
    ) or 0)


async def dlq_count() -> int:
    return int(await fetch_val(
        "SELECT COUNT(*) FROM fazle_outbound_queue WHERE status = 'dlq'"
    ) or 0)


async def _send_with_bridge(source_bridge: str, recipient: str, body: str) -> None:
    """Raises BridgeSendError on failure. Honors breaker. Honors OUTBOUND_ENABLED."""
    from app.bridge import get_bridge1, get_bridge2, BridgeSendError
    client = get_bridge2() if source_bridge == "bridge2" else get_bridge1()

    if not client.breaker.allow():
        log.warning(f"[OUTBOUND_SEND_FAIL] circuit_open bridge={source_bridge} recipient={recipient}")
        raise BridgeSendError(f"circuit_open:{source_bridge}")

    if not _outbound_enabled():
        # Stub mode: count as success without hitting network
        client.breaker.record_success()
        log.info(f"[OUTBOUND_SEND_STUB] bridge={source_bridge} recipient={recipient} body={body[:60]!r}")
        return

    log.info(f"[OUTBOUND_SEND_START] bridge={source_bridge} recipient={recipient} body={body[:60]!r}")
    try:
        await client.send_strict(recipient, body)
        client.breaker.record_success()
        log.info(f"[OUTBOUND_SEND_SUCCESS] bridge={source_bridge} recipient={recipient}")
    except BridgeSendError as exc:
        just_opened = client.breaker.record_failure()
        log.error(f"[OUTBOUND_SEND_FAIL] bridge={source_bridge} recipient={recipient} error={exc}")
        if just_opened:
            await _alert_circuit_open(source_bridge)
        raise


async def _alert_circuit_open(source_bridge: str) -> None:
    """Enqueue a one-per-minute alert to admin when a breaker opens."""
    admin = _admin_number()
    if not admin:
        return
    from datetime import datetime
    minute = datetime.utcnow().strftime("%Y%m%d%H%M")
    key = f"circuit-open-{source_bridge}-{minute}"
    other = "bridge1" if source_bridge == "bridge2" else "bridge2"
    body = f"⚠️ ALERT: {source_bridge} circuit OPEN. Outbound paused 60s. Investigate."
    try:
        await enqueue(admin, body,
                      source_bridge=other,  # send via the other bridge
                      purpose="circuit-alert",
                      idempotency_key=key)
    except Exception as e:
        log.warning(f"[outbound] failed to enqueue circuit alert: {e}")


async def sweep_once(limit: int = 20) -> dict:
    """Pick up to N due rows, send them, update status. Returns counts."""
    pool = get_pool()
    sent = failed = dlq = 0
    async with pool.acquire() as conn:
        async with conn.transaction():
            rows = await conn.fetch(
                """SELECT id, recipient, body, source_bridge, attempts, max_attempts
                     FROM fazle_outbound_queue
                    WHERE status IN ('pending','failed')
                      AND next_retry_at <= NOW()
                    ORDER BY next_retry_at
                    FOR UPDATE SKIP LOCKED
                    LIMIT $1""",
                limit,
            )
            ids = [r["id"] for r in rows]
            if ids:
                await conn.execute(
                    "UPDATE fazle_outbound_queue SET status='sending' WHERE id = ANY($1::bigint[])",
                    ids,
                )

        # Process outside the lock transaction; each result is its own update
        for r in rows:
            rid = r["id"]
            attempts = r["attempts"] + 1
            try:
                await _send_with_bridge(r["source_bridge"], r["recipient"], r["body"])
                await conn.execute(
                    """UPDATE fazle_outbound_queue
                          SET status='sent', attempts=$2, sent_at=NOW(), last_error=NULL
                        WHERE id=$1""",
                    rid, attempts,
                )
                sent += 1
            except Exception as e:
                err = str(e)[:500]
                if attempts >= r["max_attempts"]:
                    await conn.execute(
                        """UPDATE fazle_outbound_queue
                              SET status='dlq', attempts=$2, last_error=$3
                            WHERE id=$1""",
                        rid, attempts, err,
                    )
                    dlq += 1
                    await record_error("outbound.dlq", e)
                else:
                    # Exponential backoff: 1m, 2m, 4m, ...
                    await conn.execute(
                        """UPDATE fazle_outbound_queue
                              SET status='failed', attempts=$2, last_error=$3,
                                  next_retry_at = NOW() + (INTERVAL '1 minute' * (2 ^ $2))
                            WHERE id=$1""",
                        rid, attempts, err,
                    )
                    failed += 1
    return {"picked": len(rows), "sent": sent, "failed": failed, "dlq": dlq}


async def _worker_loop(interval_seconds: int) -> None:
    log.info(f"[outbound] worker started (interval={interval_seconds}s, enabled={_outbound_enabled()})")
    while True:
        try:
            await sweep_once(limit=20)
        except asyncio.CancelledError:
            log.info("[outbound] worker cancelled")
            raise
        except Exception as e:
            log.exception(f"[outbound] sweep error: {e}")
            await record_error("outbound.sweep", e)
        await asyncio.sleep(interval_seconds)


def start_background_worker(interval_seconds: int = 10) -> asyncio.Task:
    global _worker_task
    if _worker_task is not None and not _worker_task.done():
        return _worker_task
    interval = int(os.getenv("OUTBOUND_SWEEP_INTERVAL_S", str(interval_seconds)))
    _worker_task = asyncio.create_task(_worker_loop(interval), name="outbound-worker")
    return _worker_task


async def stop_background_worker() -> None:
    global _worker_task
    if _worker_task is None:
        return
    _worker_task.cancel()
    try:
        await _worker_task
    except (asyncio.CancelledError, Exception):
        pass
    _worker_task = None
