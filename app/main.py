"""
Fazle Core — Main FastAPI Application
Handles: Meta WhatsApp webhook, Bridge1, Bridge2, Send APIs, Dashboard

All message routing is delegated to modules.message_router.process_message().
This file only handles HTTP transport, signature verification, and delivery.

SAFE MODE: AUTO_REPLY_ENABLED=false suppresses all outgoing messages.
Admin command confirmations are still logged (but not sent) in safe mode.
"""
import hashlib
import hmac
import json
import logging
import time
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request, Response, Depends
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import APIKeyHeader

from app.config import get_settings
from app.database import init_db, close_db, fetch_one, fetch_all, execute
from app.bridge import get_bridge1, get_bridge2
from app import ollama as ai
from modules.intent import classify
from modules.bridge_poller import start_pollers
from modules.escort_slip_extractor import extract_escort_slip, test_report as escort_test_report
from modules.payment_workflow import create_escort_payment_draft, finalize_payment, create_advance_request_draft
from modules.message_router import process_message, get_primary_admin

log = logging.getLogger("fazle.app")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

settings = get_settings()

# ── API Key dependency ─────────────────────────────────────────────────────────
API_KEY_HEADER = APIKeyHeader(name="X-Internal-Key", auto_error=False)


def require_api_key(key: str = Depends(API_KEY_HEADER)):
    if key != settings.internal_api_key:
        raise HTTPException(status_code=403, detail="Unauthorized")
    return key


# ── Lifespan ───────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await start_pollers()
    log.info("Fazle Core started")
    yield
    await close_db()
    log.info("Fazle Core stopped")


app = FastAPI(title="Fazle Core", version="1.0.0", lifespan=lifespan)


# ── Health ─────────────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    b1 = await get_bridge1().status()
    b2 = await get_bridge2().status()
    ollama_status = await ai.check_ollama_health()
    return {
        "status": "ok",
        "bridge1": b1,
        "bridge2": b2,
        "ollama": ollama_status,
        "ts": int(time.time()),
    }


# ── Meta Webhook Verification (GET) ───────────────────────────────────────────
@app.get("/webhook/meta")
async def meta_verify(request: Request):
    params = dict(request.query_params)
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")
    if mode == "subscribe" and token == settings.meta_verify_token:
        log.info("Meta webhook verified")
        return Response(content=challenge, media_type="text/plain")
    raise HTTPException(status_code=403, detail="Verification failed")


# ── Meta Webhook Events (POST) ────────────────────────────────────────────────
@app.post("/webhook/meta")
async def meta_webhook(request: Request):
    """Receive Meta WhatsApp Cloud API events."""
    body_bytes = await request.body()

    # Verify signature
    sig = request.headers.get("X-Hub-Signature-256", "")
    if sig and settings.meta_app_secret:
        expected = "sha256=" + hmac.new(
            settings.meta_app_secret.encode(),
            body_bytes,
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(sig, expected):
            raise HTTPException(status_code=403, detail="Invalid signature")

    try:
        payload: dict[str, Any] = json.loads(body_bytes)
    except Exception:
        raise HTTPException(status_code=400, detail="Bad JSON")

    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for msg in value.get("messages", []):
                await _handle_meta_message(msg, value)

    return {"status": "ok"}


async def _handle_meta_message(msg: dict, value: dict):
    sender = msg.get("from", "")
    msg_type = msg.get("type", "")
    text = ""

    if msg_type == "text":
        text = msg.get("text", {}).get("body", "").strip()
    elif msg_type in ("image", "audio", "document", "video"):
        text = f"[{msg_type} message]"

    log.info(f"[META] from={sender} type={msg_type} text={text[:60]!r}")

    if not text:
        return

    await _save_message("meta", sender, text, direction="inbound")
    reply, send_to_admin = await _process_message(sender, text, "meta")

    if reply:
        if settings.auto_reply_enabled:
            await _send_meta(sender, reply)
            await _save_message("meta", sender, reply, direction="outbound")
        else:
            log.warning(f"SAFE MODE: reply suppressed for {sender} (meta). Saving draft.")
            intent = classify(text)
            await _save_draft("meta", sender, reply, intent)

    # send_to_admin is a dict: {admin_phone, text, bridge} — used for payment drafts
    if send_to_admin:
        await _notify_admin(send_to_admin)


# ── Bridge 1 Webhook ───────────────────────────────────────────────────────────
@app.post("/webhook/mcp1")
async def bridge1_webhook(request: Request):
    """Receive events from Bridge 1 (HR number)."""
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Bad JSON")
    await _handle_bridge_event(payload, source="bridge1")
    return {"status": "ok"}


# ── Bridge 2 Webhook ───────────────────────────────────────────────────────────
@app.post("/webhook/mcp2")
async def bridge2_webhook(request: Request):
    """Receive events from Bridge 2 (OPS number)."""
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Bad JSON")
    await _handle_bridge_event(payload, source="bridge2")
    return {"status": "ok"}


async def _handle_bridge_event(payload: dict, source: str):
    events = payload if isinstance(payload, list) else [payload]
    for event in events:
        sender = event.get("sender") or event.get("from", "")
        text = event.get("text") or event.get("message", "")
        if not text or not sender:
            continue

        sender_clean = sender.replace("@s.whatsapp.net", "").replace("+", "")
        log.info(f"[{source.upper()}] from={sender_clean} text={text[:60]!r}")

        await _save_message(source, sender_clean, text, direction="inbound")
        reply, send_to_admin = await _process_message(sender_clean, text, source)

        if reply:
            if settings.auto_reply_enabled:
                bridge = get_bridge1() if source == "bridge1" else get_bridge2()
                await bridge.send(sender, reply)
                await _save_message(source, sender_clean, reply, direction="outbound")
            else:
                log.warning(f"SAFE MODE: reply suppressed for {sender_clean} ({source}).")
                intent = classify(text)
                await _save_draft(source, sender_clean, reply, intent)

        if send_to_admin:
            await _notify_admin(send_to_admin)


# ── Unified message processor — delegates to modules.message_router ────────────

async def _process_message(sender: str, text: str, source: str) -> tuple[str, dict | None]:
    return await process_message(sender, text, source)


# ── Send APIs (protected) ──────────────────────────────────────────────────────
@app.post("/send/meta", dependencies=[Depends(require_api_key)])
async def send_meta(body: dict):
    to = body.get("to", "")
    text = body.get("text", "")
    if not to or not text:
        raise HTTPException(status_code=400, detail="Missing to/text")
    ok = await _send_meta(to, text)
    return {"sent": ok}


@app.post("/send/mcp1", dependencies=[Depends(require_api_key)])
async def send_mcp1(body: dict):
    to = body.get("to", "")
    text = body.get("text", "")
    if not to or not text:
        raise HTTPException(status_code=400, detail="Missing to/text")
    ok = await get_bridge1().send(to, text)
    return {"sent": ok}


@app.post("/send/mcp2", dependencies=[Depends(require_api_key)])
async def send_mcp2(body: dict):
    to = body.get("to", "")
    text = body.get("text", "")
    if not to or not text:
        raise HTTPException(status_code=400, detail="Missing to/text")
    ok = await get_bridge2().send(to, text)
    return {"sent": ok}


# ── Payment draft API (for manual trigger) ─────────────────────────────────────
@app.post("/payment/escort-draft", dependencies=[Depends(require_api_key)])
async def api_create_escort_draft(body: dict):
    """Manually trigger escort payment draft creation."""
    employee_id = body.get("employee_id")
    program_id  = body.get("escort_program_id")
    days        = body.get("duty_days")
    if not employee_id:
        raise HTTPException(status_code=400, detail="Missing employee_id")
    result = await create_escort_payment_draft(employee_id, program_id, days)
    if result.get("error"):
        raise HTTPException(status_code=422, detail=result["error"])
    return result


@app.post("/payment/advance-draft", dependencies=[Depends(require_api_key)])
async def api_create_advance_draft(body: dict):
    """Manually trigger advance payment draft creation."""
    employee_id = body.get("employee_id")
    amount      = body.get("amount")
    if not employee_id:
        raise HTTPException(status_code=400, detail="Missing employee_id")
    result = await create_advance_request_draft(employee_id, amount)
    if result.get("error"):
        raise HTTPException(status_code=422, detail=result["error"])
    return result


# ── Simple Dashboard ───────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    try:
        emp_count     = await fetch_one("SELECT COUNT(*) as n FROM wbom_employees WHERE status='active'")
        contact_count = await fetch_one("SELECT COUNT(*) as n FROM wbom_contacts")
        msg_count     = await fetch_one("SELECT COUNT(*) as n FROM wbom_whatsapp_messages")
        escort_count  = await fetch_one("SELECT COUNT(*) as n FROM wbom_escort_programs")
        try:
            recruit_count = await fetch_one("SELECT COUNT(*) as n FROM fazle_recruitment_sessions")
            draft_count   = await fetch_one("SELECT COUNT(*) as n FROM fazle_draft_replies WHERE status='pending' OR status IS NULL")
            pay_draft_count = await fetch_one("SELECT COUNT(*) as n FROM fazle_payment_drafts WHERE status='pending'")
        except Exception:
            recruit_count = draft_count = pay_draft_count = {"n": "?"}
    except Exception:
        emp_count = contact_count = msg_count = escort_count = recruit_count = draft_count = pay_draft_count = {"n": "?"}

    b1 = await get_bridge1().status()
    b2 = await get_bridge2().status()

    safe_banner = "" if settings.auto_reply_enabled else (
        "<div style='background:#e74c3c;color:#fff;padding:12px;border-radius:6px;margin:10px 0;font-weight:bold'>"
        "🔒 SAFE MODE ACTIVE — No outgoing messages will be sent.</div>"
    )

    html = f"""<!DOCTYPE html>
<html lang="bn">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Fazle Core Dashboard</title>
<style>
body{{font-family:Arial,sans-serif;background:#f5f5f5;margin:0;padding:20px}}
h1{{color:#1a472a;font-size:24px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:16px;margin:20px 0}}
.card{{background:#fff;border-radius:8px;padding:16px;box-shadow:0 2px 6px rgba(0,0,0,.1);text-align:center}}
.card .num{{font-size:36px;font-weight:bold;color:#2d6a4f}}
.card .label{{color:#555;font-size:13px;margin-top:4px}}
.bridge{{background:#fff;border-radius:8px;padding:12px;margin:8px 0;box-shadow:0 1px 4px rgba(0,0,0,.1)}}
.ok{{color:green}} .err{{color:red}}
</style>
</head>
<body>
<h1>🌿 Fazle Core — Al-Aqsa Security</h1>
{safe_banner}
<div class="grid">
  <div class="card"><div class="num">{emp_count['n']}</div><div class="label">Active Employees</div></div>
  <div class="card"><div class="num">{contact_count['n']}</div><div class="label">Contacts</div></div>
  <div class="card"><div class="num">{msg_count['n']}</div><div class="label">Messages</div></div>
  <div class="card"><div class="num">{escort_count['n']}</div><div class="label">Escort Programs</div></div>
  <div class="card"><div class="num">{recruit_count['n']}</div><div class="label">Recruitment Leads</div></div>
  <div class="card"><div class="num">{draft_count['n']}</div><div class="label">Pending Drafts</div></div>
  <div class="card"><div class="num">{pay_draft_count['n']}</div><div class="label">Payment Drafts</div></div>
</div>
<h2>Bridge Status</h2>
<div class="bridge"><b>Bridge 1 (HR — {settings.bridge1_number}):</b>
  <span class="{'ok' if not b1.get('error') else 'err'}">{b1}</span>
</div>
<div class="bridge"><b>Bridge 2 (OPS — {settings.bridge2_number}):</b>
  <span class="{'ok' if not b2.get('error') else 'err'}">{b2}</span>
</div>
<h2>Admin Numbers</h2>
<div class="bridge">Meta Admin: <b>{settings.admin_meta_number}</b></div>
<div class="bridge">Bridge1 Admin: <b>{settings.bridge1_number}</b></div>
<div class="bridge">Bridge2 Admin: <b>{settings.bridge2_number}</b></div>
<div class="bridge">Accountant: <b>{settings.accountant_phone}</b></div>
<p style="color:#999;font-size:12px;margin-top:30px">Fazle Core v1.0 | Port {settings.app_port} | Safe Mode: {'OFF' if settings.auto_reply_enabled else 'ON'}</p>
</body></html>"""
    return HTMLResponse(html)


# ── Helpers ────────────────────────────────────────────────────────────────────
async def _send_meta(to: str, text: str) -> bool:
    url = f"{settings.meta_api_url}/{settings.meta_phone_number_id}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "text",
        "text": {"body": text},
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(
                url,
                json=payload,
                headers={"Authorization": f"Bearer {settings.meta_api_token}"},
            )
            return r.status_code == 200
    except Exception as e:
        log.error(f"Meta send error: {e}")
        return False


async def _save_message(source: str, sender: str, text: str, direction: str):
    try:
        await execute(
            """
            INSERT INTO wbom_whatsapp_messages
                (sender_number, message_body, message_type, direction, platform, is_processed, contact_identifier)
            VALUES ($1, $2, 'text', $3, $4, true, $1)
            """,
            sender, text, direction, source,
        )
    except Exception as e:
        log.warning(f"Message save error: {e}")


async def _save_draft(source: str, recipient: str, reply_text: str, intent: str):
    try:
        await execute(
            """
            INSERT INTO fazle_draft_replies
                (source, recipient, reply_text, intent, draft_only, status, created_at)
            VALUES ($1, $2, $3, $4, true, 'pending', NOW())
            """,
            source, recipient, reply_text, intent,
        )
    except Exception as e:
        log.warning(f"Draft save error: {e}")


async def _notify_admin(notification: dict):
    """
    Send a notification to admin (or accountant) via the appropriate bridge.
    Respects SAFE MODE — logs only when disabled.
    """
    admin_phone = notification.get("admin_phone", "")
    text        = notification.get("text", "")
    bridge_src  = notification.get("bridge", "bridge2")

    if not admin_phone or not text:
        return

    log.info(f"[notify_admin] to={admin_phone} bridge={bridge_src} text={text[:60]!r}")

    if not settings.auto_reply_enabled:
        log.warning(f"SAFE MODE: admin notification suppressed → {admin_phone}: {text[:100]}")
        return

    try:
        if bridge_src == "meta" or admin_phone == settings.admin_meta_number:
            await _send_meta(admin_phone, text)
        elif bridge_src == "bridge1":
            await get_bridge1().send(f"{admin_phone}@s.whatsapp.net", text)
        else:
            await get_bridge2().send(f"{admin_phone}@s.whatsapp.net", text)
    except Exception as e:
        log.error(f"[notify_admin] send error: {e}")



# ── Escort Slip Extractor API ──────────────────────────────────────────────────
@app.post("/escort-slip/extract", dependencies=[Depends(require_api_key)])
async def api_extract_escort_slip(body: dict):
    file_path    = body.get("file_path", "")
    source_label = body.get("source_label", "api_upload")
    if not file_path:
        raise HTTPException(status_code=400, detail="Missing file_path")
    result = await extract_escort_slip(file_path, source_label=source_label, save_to_db=True)
    return result


@app.post("/escort-slip/test-report", dependencies=[Depends(require_api_key)])
async def api_escort_test_report(body: dict):
    file_path = body.get("file_path", "")
    if not file_path:
        raise HTTPException(status_code=400, detail="Missing file_path")
    report = await escort_test_report(file_path)
    return {"report": report}


@app.get("/escort-slip/extractions", dependencies=[Depends(require_api_key)])
async def list_escort_extractions(limit: int = 20):
    rows = await fetch_all(
        "SELECT id, created_at, document_type, mother_vessel, lighter_vessel, "
        "escort_name, start_date, completion_date, confidence "
        "FROM escort_slip_extractions ORDER BY created_at DESC LIMIT $1",
        limit,
    )
    return {"count": len(rows), "extractions": rows}


# ── Admin APIs ─────────────────────────────────────────────────────────────────
@app.get("/admin/safe-mode", dependencies=[Depends(require_api_key)])
async def safe_mode_status():
    return {
        "auto_reply_enabled": settings.auto_reply_enabled,
        "safe_mode_active": not settings.auto_reply_enabled,
        "note": "Change AUTO_REPLY_ENABLED in .env and restart to toggle.",
    }


@app.get("/admin/drafts", dependencies=[Depends(require_api_key)])
async def list_drafts(limit: int = 50):
    try:
        rows = await fetch_all(
            "SELECT * FROM fazle_draft_replies WHERE draft_only=true ORDER BY created_at DESC LIMIT $1",
            limit,
        )
        return {"count": len(rows), "drafts": rows}
    except Exception as e:
        return {"error": str(e), "drafts": []}


@app.get("/admin/payment-drafts", dependencies=[Depends(require_api_key)])
async def list_payment_draft_api(limit: int = 20):
    try:
        rows = await fetch_all(
            "SELECT * FROM fazle_payment_drafts ORDER BY created_at DESC LIMIT $1",
            limit,
        )
        return {"count": len(rows), "payment_drafts": rows}
    except Exception as e:
        return {"error": str(e), "payment_drafts": []}


@app.get("/admin/recruitment", dependencies=[Depends(require_api_key)])
async def list_recruitment(limit: int = 50):
    try:
        rows = await fetch_all(
            "SELECT id, phone, full_name, age, area, job_preference, score, score_bucket, funnel_stage, created_at "
            "FROM fazle_recruitment_sessions ORDER BY created_at DESC LIMIT $1",
            limit,
        )
        return {"count": len(rows), "leads": rows}
    except Exception as e:
        return {"error": str(e), "leads": []}
