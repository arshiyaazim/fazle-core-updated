"""Internal admin API for social auto-reply."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import APIKeyHeader

from app.config import get_settings
from app.database import execute

from . import send_queue, state_tracker

router = APIRouter(prefix="/api/social", tags=["social-auto-reply"])
_API_KEY_HEADER = APIKeyHeader(name="X-Internal-Key", auto_error=False)


async def _require_api_key(key: str = Depends(_API_KEY_HEADER)):
    settings = get_settings()
    if key and key == settings.internal_api_key:
        return key
    if key:
        try:
            from modules import rbac
            admin = await rbac.get_admin_by_api_key(key)
            if admin and admin.get("status") == "active":
                return key
        except Exception:
            pass
    raise HTTPException(status_code=403, detail="Unauthorized")


@router.get("/status", dependencies=[Depends(_require_api_key)])
async def social_status():
    return await state_tracker.status()


@router.get("/queue", dependencies=[Depends(_require_api_key)])
async def social_queue(limit: int = 50):
    return {"items": await state_tracker.queue_rows(limit)}


@router.get("/flagged", dependencies=[Depends(_require_api_key)])
async def social_flagged(limit: int = 50):
    return {"items": await state_tracker.flagged_rows(limit)}


@router.post("/pause", dependencies=[Depends(_require_api_key)])
async def social_pause():
    await state_tracker.set_paused(True, "api")
    return {"paused": True}


@router.post("/resume", dependencies=[Depends(_require_api_key)])
async def social_resume():
    await state_tracker.set_paused(False, "api")
    return {"paused": False}


@router.post("/retry", dependencies=[Depends(_require_api_key)])
async def social_retry(queue_id: int | None = None):
    if queue_id is not None:
        await execute(
            "UPDATE social_reply_queue SET status='pending', next_retry_at=NOW(), last_error=NULL WHERE id=$1 AND status IN ('failed','blocked','dlq')",
            queue_id,
        )
    return await send_queue.sweep_once(limit=1)
