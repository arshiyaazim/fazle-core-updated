"""
Fazle Core — Main FastAPI Application
Handles: Meta WhatsApp webhook, Bridge1, Bridge2, Send APIs, Dashboard

All message routing is delegated to modules.message_router.process_message().
This file only handles HTTP transport, signature verification, and delivery.

SAFE MODE: AUTO_REPLY_ENABLED=false suppresses all outgoing messages.
Admin command confirmations are still logged (but not sent) in safe mode.
"""
import asyncio
import hashlib
import hmac
import json
import logging
import os
import shutil
import time
from contextlib import asynccontextmanager
from typing import Any, Optional

import httpx
from fastapi import FastAPI, HTTPException, Request, Response, Depends
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.security import APIKeyHeader

from app.config import get_settings
from app.database import init_db, close_db, fetch_one, fetch_all, fetch_val, execute
from app.bridge import get_bridge1, get_bridge2
from app.logging_setup import setup_logging
from app import ollama as ai
from modules.intent import classify
from modules.bridge_poller import start_pollers
from modules.escort_slip_extractor import extract_escort_slip, test_report as escort_test_report
from modules.payment_workflow import create_escort_payment_draft, finalize_payment, create_advance_request_draft
from modules.message_router import process_message, get_primary_admin
from modules.recruitment_flow import is_recruitment_trigger, get_active_session
from modules import outbound as outbound_queue
from modules import scheduler as fazle_scheduler

setup_logging()
log = logging.getLogger("fazle.app")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

settings = get_settings()

# ── Concurrency caps (B15.6) ───────────────────────────────────────────────────
OCR_SEMAPHORE = asyncio.Semaphore(int(os.getenv("OCR_CONCURRENCY", "2")))
BULK_COMPUTE_SEMAPHORE = asyncio.Semaphore(int(os.getenv("PAYROLL_BULK_CONCURRENCY", "1")))

# ── Feature flag: queue-based outbound (B15.9) ─────────────────────────────────
def _use_outbound_queue() -> bool:
    return os.getenv("USE_OUTBOUND_QUEUE", "true").lower() in ("1", "true", "yes")

# ── API Key dependency ─────────────────────────────────────────────────────────
API_KEY_HEADER = APIKeyHeader(name="X-Internal-Key", auto_error=False)


async def require_api_key(key: str = Depends(API_KEY_HEADER)):
    # Legacy single-key (env INTERNAL_API_KEY) — always accepted
    if key and key == settings.internal_api_key:
        return key
    # Batch 19 — per-admin API keys (sha256 lookup)
    if key:
        try:
            from modules import rbac
            admin = await rbac.get_admin_by_api_key(key)
            if admin and admin.get("status") == "active":
                return key
        except Exception as e:  # rbac unavailable → fail closed below
            log.warning(f"[auth] rbac key lookup failed: {e}")
    raise HTTPException(status_code=403, detail="Unauthorized")


# ── Lifespan ───────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    # Batch 19 — bootstrap admin users from ADMIN_NUMBERS env
    try:
        from modules import rbac
        n = await rbac.ensure_bootstrap_admins()
        log.info(f"[rbac] bootstrap complete (created={n})")
    except Exception as e:
        log.warning(f"[rbac] bootstrap failed: {e}")
    await start_pollers()
    outbound_queue.start_background_worker()
    fazle_scheduler.start_scheduler()
    # Batch 21 — build RAG index in background (non-fatal)
    try:
        from modules import rag
        asyncio.create_task(rag.build_index())
        log.info("[rag] index build scheduled")
    except Exception as e:
        log.warning(f"[rag] startup build failed to schedule: {e}")
    log.info("Fazle Core started")
    yield
    fazle_scheduler.stop_scheduler()
    await outbound_queue.stop_background_worker()
    await close_db()
    log.info("Fazle Core stopped")


app = FastAPI(title="Fazle Core", version="1.0.0", lifespan=lifespan)


# ── Batch 22 — observability middleware ───────────────────────────────────────
from modules import observability as obs
from starlette.requests import Request as _Req


def _route_template(request: _Req) -> str:
    """Return the matched route template (e.g. /admin/users/{phone}/role)
    so cardinality stays bounded. Falls back to raw path."""
    try:
        route = request.scope.get("route")
        if route is not None and getattr(route, "path", None):
            return route.path
    except Exception:
        pass
    return request.url.path


@app.middleware("http")
async def metrics_middleware(request: _Req, call_next):
    t0 = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        dur_ms = (time.perf_counter() - t0) * 1000.0
        path = _route_template(request)
        # Skip the metrics endpoint itself to avoid feedback loops
        if path not in ("/metrics", "/metrics/json"):
            obs.inc(
                "fazle_http_requests_total",
                labels={"method": request.method, "path": path, "status": str(status)},
            )
            obs.observe(
                "fazle_http_request_duration_ms",
                dur_ms,
                labels={"method": request.method, "path": path},
            )


# ── Batch 11 — recruitment-only auto-reply gate ────────────────────────────────
def _admin_numbers_set() -> set[str]:
    return {
        settings.admin_meta_number,
        settings.admin_bridge1_number,
        settings.admin_bridge2_number,
        *settings.admin_number_list,
    }


async def _should_recruitment_autoreply(sender_clean: str, text: str) -> bool:
    """Return True iff SAFE MODE should be bypassed for this single message
    because it is a recruitment intake (trigger word OR active session).
    Admin senders are always excluded."""
    if not settings.recruitment_autoreply_enabled:
        return False
    if sender_clean in _admin_numbers_set():
        return False
    if is_recruitment_trigger(text):
        return True
    try:
        if await get_active_session(sender_clean):
            return True
    except Exception as e:  # never block flow on session lookup
        log.debug(f"recruitment session check failed for {sender_clean}: {e}")
    return False


# ── Health (B15.5 expanded) ───────────────────────────────────────────────────
async def _probe_db() -> dict:
    try:
        v = await asyncio.wait_for(fetch_val("SELECT 1"), timeout=2.0)
        return {"status": "ok", "value": int(v or 0)}
    except Exception as e:
        return {"status": "critical", "error": str(e)[:200]}


def _probe_file_age(path: str, label: str) -> dict:
    """Best-effort liveness via mtime. Bridge DBs only write on new inbound msgs,
    so quiet hours are normal — we never escalate to 'critical' here."""
    try:
        st = os.stat(path)
        age = int(time.time() - st.st_mtime)
        # Only mark degraded after long idle; never critical (bridges may simply be quiet)
        status = "ok" if age < 3600 else "degraded"
        return {"status": status, "mtime_age_s": age}
    except FileNotFoundError:
        return {"status": "critical", "error": f"{label} db not found"}
    except Exception as e:
        return {"status": "critical", "error": str(e)[:200]}


async def _probe_heartbeat(service: str, stale_after_s: int = 120) -> dict:
    try:
        row = await fetch_one(
            "SELECT EXTRACT(EPOCH FROM (NOW() - last_seen))::INT AS age, last_message_id, queue_depth "
            "FROM fazle_service_heartbeats WHERE service = $1",
            service,
        )
        if not row:
            return {"status": "critical", "error": "no heartbeat ever"}
        age = int(row["age"])
        status = "ok" if age < stale_after_s else ("degraded" if age < stale_after_s * 3 else "critical")
        return {"status": status, "age_s": age, "last_message_id": row["last_message_id"], "queue_depth": row["queue_depth"]}
    except Exception as e:
        return {"status": "critical", "error": str(e)[:200]}


async def _probe_outbound() -> dict:
    try:
        pend = await outbound_queue.pending_count()
        dlq = await outbound_queue.dlq_count()
        status = "ok"
        if dlq > 0:
            status = "degraded"
        if pend > 200:
            status = "degraded"
        if dlq > 50:
            status = "critical"
        return {"status": status, "pending": pend, "dlq": dlq}
    except Exception as e:
        return {"status": "critical", "error": str(e)[:200]}


def _probe_disk() -> dict:
    try:
        usage = shutil.disk_usage("/")
        pct = int(usage.used * 100 / usage.total)
        warn = int(os.getenv("HEALTH_DISK_WARN_PCT", "80"))
        crit = int(os.getenv("HEALTH_DISK_CRIT_PCT", "90"))
        status = "ok" if pct < warn else ("degraded" if pct < crit else "critical")
        return {"status": status, "used_pct": pct, "free_gb": round(usage.free / 1e9, 1)}
    except Exception as e:
        return {"status": "critical", "error": str(e)[:200]}


def _probe_mem() -> dict:
    try:
        with open("/proc/meminfo") as f:
            info = {}
            for line in f:
                k, _, rest = line.partition(":")
                info[k.strip()] = int(rest.strip().split()[0])  # kB
        avail_mb = info.get("MemAvailable", 0) // 1024
        crit = int(os.getenv("HEALTH_MEM_CRIT_MB", "200"))
        status = "ok" if avail_mb > crit * 2 else ("degraded" if avail_mb > crit else "critical")
        return {"status": status, "available_mb": avail_mb}
    except Exception as e:
        return {"status": "critical", "error": str(e)[:200]}


async def _build_health(deep: bool = False) -> dict:
    db_p, ollama_p, ob_p, hb1_p, hb2_p = await asyncio.gather(
        _probe_db(),
        _safe_ollama(),
        _probe_outbound(),
        _probe_heartbeat("bridge_poller:bridge1"),
        _probe_heartbeat("bridge_poller:bridge2"),
        return_exceptions=False,
    )
    bridge1_db = _probe_file_age("/home/azim/whatsapp-mcp/whatsapp-bridge/store/messages.db", "bridge1")
    bridge2_db = _probe_file_age("/home/azim/whatsapp2/store/messages.db", "bridge2")
    disk_p = _probe_disk()
    mem_p = _probe_mem()

    probes = {
        "db": db_p,
        "bridge1_db": bridge1_db,
        "bridge2_db": bridge2_db,
        "bridge_poller_b1": hb1_p,
        "bridge_poller_b2": hb2_p,
        "outbound": ob_p,
        "disk": disk_p,
        "mem": mem_p,
        "ollama": ollama_p,
    }
    statuses = [p.get("status", "ok") for p in probes.values()]
    if "critical" in statuses:
        overall = "critical"
    elif "degraded" in statuses:
        overall = "degraded"
    else:
        overall = "ok"

    out: dict[str, Any] = {"status": overall, "probes": probes, "ts": int(time.time())}
    if deep:
        try:
            out["bridges"] = {
                "bridge1": await get_bridge1().status(),
                "bridge2": await get_bridge2().status(),
            }
        except Exception as e:
            out["bridges"] = {"error": str(e)[:200]}
    return out


async def _safe_ollama() -> dict:
    try:
        s = await ai.check_ollama_health()
        if isinstance(s, dict):
            s.setdefault("status", "ok")
            return s
        return {"status": "ok", "raw": str(s)[:200]}
    except Exception as e:
        return {"status": "degraded", "error": str(e)[:200]}


@app.get("/health")
async def health():
    out = await _build_health(deep=False)
    if out["status"] == "critical":
        return JSONResponse(status_code=503, content=out)
    return out


@app.get("/health/deep", dependencies=[Depends(require_api_key)])
async def health_deep():
    out = await _build_health(deep=True)
    if out["status"] == "critical":
        return JSONResponse(status_code=503, content=out)
    return out


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
        recruit_gate = (not settings.auto_reply_enabled
                        and await _should_recruitment_autoreply(sender, text))
        if settings.auto_reply_enabled or recruit_gate:
            if recruit_gate:
                log.info(f"[RECRUIT-AUTOREPLY] sending to {sender} (meta) despite SAFE MODE")
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
        _forward_to_agent_if_admin(sender_clean, text, source)
        reply, send_to_admin = await _process_message(sender_clean, text, source)

        if reply:
            recruit_gate = (not settings.auto_reply_enabled
                            and await _should_recruitment_autoreply(sender_clean, text))
            if settings.auto_reply_enabled or recruit_gate:
                if recruit_gate:
                    log.info(f"[RECRUIT-AUTOREPLY] sending to {sender_clean} ({source}) despite SAFE MODE")
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

# Phones forwarded to fazle-agent /admin/inbox for Tier-1 NL handling.
_AGENT_FORWARD_PHONES = {"8801880446111", "8801958122300"}
_AGENT_INBOX_URL = "http://127.0.0.1:8300/admin/inbox"


def _forward_to_agent_if_admin(sender_clean: str, text: str, source: str) -> None:
    """Fire-and-forget forward to fazle-agent. Never raises, never blocks."""
    if sender_clean not in _AGENT_FORWARD_PHONES:
        return
    bridge = "1" if source == "bridge1" else "2"
    payload = {"from": sender_clean, "text": text, "bridge": bridge}

    async def _send():
        try:
            async with httpx.AsyncClient(timeout=4.0) as client:
                await client.post(_AGENT_INBOX_URL, json=payload)
        except Exception as e:
            log.debug(f"agent forward failed (shadow mode): {e}")

    try:
        asyncio.create_task(_send())
    except Exception as e:
        log.debug(f"agent forward task spawn failed: {e}")


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


@app.post("/payment/ingest", dependencies=[Depends(require_api_key)])
async def api_payment_ingest(body: dict):
    """Batch 12: Ingest a bKash/Nagad/Rocket SMS or admin paste.
    Body: {text: str, sender_number?: str, message_id?: int, auto_finalize?: bool}
    """
    from modules.payment_ingest import ingest_payment_sms
    text = (body.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Missing text")
    res = await ingest_payment_sms(
        text,
        sender_number=body.get("sender_number"),
        message_id=body.get("message_id"),
        auto_finalize=bool(body.get("auto_finalize", True)),
    )
    if not res.get("ok"):
        raise HTTPException(status_code=422, detail=res)
    return res


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


# ── Simple Dashboard (legacy server-rendered) ─────────────────────────────────
@app.get("/", response_class=HTMLResponse)
@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_spa():
    """Batch 20 — multi-tab admin SPA shell. JS prompts for API key."""
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "dashboard.html"))


@app.get("/dashboard/legacy", response_class=HTMLResponse)
async def dashboard_legacy():
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

    # B15.9: route through outbound queue when enabled
    if _use_outbound_queue() and bridge_src in ("bridge1", "bridge2"):
        try:
            idem = notification.get("idempotency_key")
            await outbound_queue.enqueue(
                admin_phone, text,
                source_bridge=bridge_src,
                purpose=notification.get("purpose", "admin-notify"),
                idempotency_key=idem,
                meta={"caller": "notify_admin"},
            )
            return
        except Exception as e:
            log.error(f"[notify_admin] enqueue error, falling back to direct: {e}")

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
    auto_close   = bool(body.get("auto_close", True))
    if not file_path:
        raise HTTPException(status_code=400, detail="Missing file_path")
    # B15.6 — cap concurrent OCR + 30s timeout
    try:
        await asyncio.wait_for(OCR_SEMAPHORE.acquire(), timeout=30.0)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=429, detail="ocr busy, retry later")
    try:
        result = await extract_escort_slip(file_path, source_label=source_label, save_to_db=True)
    finally:
        OCR_SEMAPHORE.release()

    # Batch 13: if extraction confident enough and auto_close=true, run lifecycle
    if auto_close and isinstance(result, dict):
        mobile = result.get("mobile") or result.get("escort_mobile")
        if mobile:
            mob_norm = "".join(ch for ch in str(mobile) if ch.isdigit())[-11:]
            emp = await fetch_one(
                "SELECT employee_id FROM wbom_employees "
                "WHERE regexp_replace(employee_mobile,'\\D','','g') LIKE '%'||$1 LIMIT 1",
                mob_norm,
            )
            if emp:
                from modules.escort_lifecycle import handle_release_event
                rel = await handle_release_event(
                    int(emp["employee_id"]), extracted=result, source="escort-slip-ocr",
                )
                result["lifecycle"] = rel
    return result


@app.post("/escort/release", dependencies=[Depends(require_api_key)])
async def api_escort_release(body: dict):
    """Batch 13: manual escort lifecycle close + draft creation.
    Body: {employee_id, end_date?, end_shift?, release_point?, day_count?}
    """
    from modules.escort_lifecycle import handle_release_event
    eid = body.get("employee_id")
    if not eid:
        raise HTTPException(status_code=400, detail="Missing employee_id")
    extracted = {
        "end_date": body.get("end_date"),
        "end_shift": body.get("end_shift"),
        "release_point": body.get("release_point"),
        "day_count": body.get("day_count"),
    }
    result = await handle_release_event(int(eid), extracted=extracted,
                                         source=body.get("source", "api"))
    if not result.get("ok"):
        raise HTTPException(status_code=422, detail=result)
    return result


# ── Payroll API (Batch 14) ─────────────────────────────────────────────────────
@app.post("/payroll/compute", dependencies=[Depends(require_api_key)])
async def api_payroll_compute(body: dict):
    """Body: {period_year, period_month, employee_id?, computed_by?}.
    If employee_id missing, compute for all Active employees.
    """
    from modules.payroll import compute_run, compute_all_for_period
    y = int(body.get("period_year") or 0)
    m = int(body.get("period_month") or 0)
    actor = body.get("computed_by") or "api"
    if not (1 <= m <= 12) or y < 2020:
        raise HTTPException(status_code=400, detail="invalid period")
    eid = body.get("employee_id")
    if eid:
        r = await compute_run(int(eid), y, m, actor)
    else:
        # B15.6 — serialize bulk computes
        async with BULK_COMPUTE_SEMAPHORE:
            r = await compute_all_for_period(y, m, actor)
    if not r.get("ok"):
        raise HTTPException(status_code=422, detail=r)
    return r


@app.post("/payroll/run/{run_id}/transition", dependencies=[Depends(require_api_key)])
async def api_payroll_transition(run_id: int, body: dict):
    """Body: {action: submit|approve|lock|paid|cancel, actor, ...}"""
    from modules.payroll import (submit_run, approve_run, lock_run,
                                  mark_paid, cancel_run)
    action = (body.get("action") or "").lower()
    actor  = body.get("actor") or "api"
    if action == "submit":
        r = await submit_run(run_id, actor)
    elif action == "approve":
        r = await approve_run(run_id, actor)
    elif action == "lock":
        r = await lock_run(run_id, actor)
    elif action == "paid":
        amount = float(body.get("amount") or 0)
        method = body.get("method") or "cash"
        ref    = body.get("reference")
        r = await mark_paid(run_id, actor, amount, method, ref)
    elif action == "cancel":
        reason = body.get("reason") or "no reason"
        r = await cancel_run(run_id, actor, reason)
    else:
        raise HTTPException(status_code=400, detail="invalid action")
    if not r.get("ok"):
        raise HTTPException(status_code=422, detail=r)
    return r


@app.get("/payroll/runs", dependencies=[Depends(require_api_key)])
async def api_payroll_list(period: str, status: str | None = None):
    """period=YYYY-MM"""
    from modules.payroll import list_runs
    try:
        y, m = period.split("-")
        y = int(y); m = int(m)
    except Exception:
        raise HTTPException(status_code=400, detail="period must be YYYY-MM")
    rows = await list_runs(y, m, status)
    return {"period": period, "status": status, "count": len(rows), "runs": rows}


@app.get("/payroll/runs/{run_id}", dependencies=[Depends(require_api_key)])
async def api_payroll_get(run_id: int):
    from modules.payroll import get_run
    r = await get_run(run_id)
    if not r:
        raise HTTPException(status_code=404, detail="not found")
    items = await fetch_all(
        "SELECT item_id, component_type, component_label, amount, sign, "
        "source_table, source_id, notes FROM wbom_payroll_run_items "
        "WHERE run_id=$1 ORDER BY item_id", run_id,
    )
    return {"run": r, "items": [dict(i) for i in items]}


# ── Scheduler API (Batch 16) ──────────────────────────────────────────────────
@app.get("/scheduler/status", dependencies=[Depends(require_api_key)])
async def api_scheduler_status():
    return await fazle_scheduler.get_status()


@app.post("/scheduler/run/{job_name}", dependencies=[Depends(require_api_key)])
async def api_scheduler_run(job_name: str):
    if job_name not in fazle_scheduler.list_job_names():
        raise HTTPException(status_code=404,
                            detail=f"unknown job; available: {fazle_scheduler.list_job_names()}")
    result = await fazle_scheduler.trigger_job(job_name)
    return {"job": job_name, "result": result}


# ── Reports API (Batch 17) ────────────────────────────────────────────────────
from modules import reports as fazle_reports  # noqa: E402

@app.get("/reports", dependencies=[Depends(require_api_key)])
async def api_reports_list():
    return {"available": fazle_reports.list_reports()}


@app.get("/reports/{name}", dependencies=[Depends(require_api_key)])
async def api_reports_run(name: str, request: Request,
                          fmt: str = "json", no_cache: bool = False):
    args = {k: v for k, v in request.query_params.items()
            if k not in ("fmt", "no_cache")}
    try:
        payload = await fazle_reports.run_report(
            name, args, requested_by="api", use_cache=not no_cache,
        )
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"report failed: {e}")
    if fmt == "csv":
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse(fazle_reports.render_csv(payload),
                                 media_type="text/csv")
    if fmt == "text":
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse(fazle_reports.render_text(payload))
    return payload


# ── BACKUP (Batch 18) ────────────────────────────────────────────────────────
from modules import backup as fazle_backup  # noqa: E402


@app.get("/backup/status", dependencies=[Depends(require_api_key)])
async def api_backup_status():
    return await fazle_backup.backup_status()


@app.get("/backup/list", dependencies=[Depends(require_api_key)])
async def api_backup_list(limit: int = 20):
    return {"backups": await fazle_backup.list_backups(limit=limit)}


@app.post("/backup/run", dependencies=[Depends(require_api_key)])
async def api_backup_run(rotate: bool = True):
    res = await fazle_backup.run_backup()
    out: dict = {"backup": res}
    if rotate and res.get("status") == "ok":
        out["rotate"] = await fazle_backup.rotate_backups()
    return out


@app.post("/backup/rotate", dependencies=[Depends(require_api_key)])
async def api_backup_rotate():
    return await fazle_backup.rotate_backups()


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


# ── Batch 19 — RBAC admin endpoints ────────────────────────────────────────────
@app.get("/admin/users", dependencies=[Depends(require_api_key)])
async def b19_list_users():
    from modules import rbac
    rows = await rbac.list_admins()
    return {"count": len(rows), "users": rows}


@app.post("/admin/users", dependencies=[Depends(require_api_key)])
async def b19_add_user(payload: dict):
    from modules import rbac
    phone = (payload or {}).get("phone")
    name  = (payload or {}).get("name")
    role  = (payload or {}).get("role", "viewer")
    granted_by = (payload or {}).get("granted_by", "http")
    if not phone or not name:
        raise HTTPException(400, "phone and name required")
    try:
        return await rbac.add_admin(phone, name, role=role, granted_by=granted_by)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/admin/users/{phone}/role", dependencies=[Depends(require_api_key)])
async def b19_set_role(phone: str, payload: dict):
    from modules import rbac
    role = (payload or {}).get("role")
    if not role:
        raise HTTPException(400, "role required")
    try:
        return await rbac.set_role(phone, role, granted_by=(payload or {}).get("granted_by", "http"))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.delete("/admin/users/{phone}/role/{role}", dependencies=[Depends(require_api_key)])
async def b19_revoke_role(phone: str, role: str):
    from modules import rbac
    try:
        return await rbac.revoke_role(phone, role)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/admin/users/{phone}/disable", dependencies=[Depends(require_api_key)])
async def b19_disable_user(phone: str):
    from modules import rbac
    try:
        return await rbac.disable_admin(phone)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/admin/users/{phone}/apikey", dependencies=[Depends(require_api_key)])
async def b19_issue_apikey(phone: str):
    from modules import rbac
    try:
        return await rbac.issue_api_key(phone)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/admin/audit", dependencies=[Depends(require_api_key)])
async def b19_audit(limit: int = 50, command: Optional[str] = None):
    from modules import rbac
    rows = await rbac.list_audit(limit=limit, command=command)
    return {"count": len(rows), "audit": rows}


# ── Batch 20 — Admin Dashboard aggregate endpoint ─────────────────────────────
@app.get("/admin/overview", dependencies=[Depends(require_api_key)])
async def b20_overview():
    """Single-roundtrip overview for the dashboard SPA."""
    from datetime import datetime, timedelta, timezone
    out: dict = {"now": datetime.now(timezone.utc).isoformat(), "errors": []}

    # safe-mode
    out["safe_mode"] = {
        "auto_reply_enabled": settings.auto_reply_enabled,
        "safe_mode_active": not settings.auto_reply_enabled,
    }

    # bridges
    try:
        b1 = await get_bridge1().status()
        b2 = await get_bridge2().status()
        out["bridges"] = {"bridge1": b1, "bridge2": b2}
    except Exception as e:
        out["bridges"] = {"error": str(e)}

    # counts
    try:
        async def _n(sql):
            r = await fetch_one(sql)
            return int(r["n"]) if r and r.get("n") is not None else 0
        out["counts"] = {
            "active_employees": await _n("SELECT COUNT(*) AS n FROM wbom_employees WHERE status='active'"),
            "contacts": await _n("SELECT COUNT(*) AS n FROM wbom_contacts"),
            "messages": await _n("SELECT COUNT(*) AS n FROM wbom_whatsapp_messages"),
            "escort_programs": await _n("SELECT COUNT(*) AS n FROM wbom_escort_programs"),
            "recruitment_leads": await _n("SELECT COUNT(*) AS n FROM fazle_recruitment_sessions"),
            "pending_drafts": await _n("SELECT COUNT(*) AS n FROM fazle_draft_replies WHERE status='pending' OR status IS NULL"),
            "pending_payment_drafts": await _n("SELECT COUNT(*) AS n FROM fazle_payment_drafts WHERE status='pending'"),
            "admin_users": await _n("SELECT COUNT(*) AS n FROM fazle_admins WHERE status='active'"),
        }
    except Exception as e:
        out["counts"] = {}
        out["errors"].append(f"counts: {e}")

    # scheduler
    try:
        from modules import scheduler as fazle_scheduler
        sched = await fazle_scheduler.get_status()
        out["scheduler"] = {
            "running": bool(sched.get("enabled")),
            "tz": sched.get("tz"),
            "jobs": len(sched.get("jobs", [])),
            "next_jobs": [
                {"id": j.get("job_name"), "next_run": j.get("next_run_at")}
                for j in sched.get("jobs", [])[:8]
            ],
        }
    except Exception as e:
        out["scheduler"] = {"error": str(e)}

    # backup
    try:
        from modules import backup as db_backup
        st = await db_backup.backup_status()
        # normalise keys for the dashboard
        out["backup"] = {
            "latest": st.get("latest"),
            "files": st.get("files_on_disk"),
            "total_bytes": st.get("total_bytes"),
            "age_hours": st.get("newest_age_h"),
            "dir": st.get("dir"),
        }
    except Exception as e:
        out["backup"] = {"error": str(e)}

    # audit (24h count + last 5)
    try:
        c = await fetch_val(
            "SELECT COUNT(*) FROM fazle_admin_audit WHERE created_at > now() - interval '24 hours'"
        )
        denied = await fetch_val(
            "SELECT COUNT(*) FROM fazle_admin_audit WHERE allowed=false AND created_at > now() - interval '24 hours'"
        )
        out["audit"] = {
            "last_24h_total": int(c or 0),
            "last_24h_denied": int(denied or 0),
        }
    except Exception as e:
        out["audit"] = {"error": str(e)}

    # rag (B21) — quick stats, non-fatal
    try:
        from modules import rag
        out["rag"] = await rag.stats()
    except Exception as e:
        out["rag"] = {"error": str(e)}

    return out


# ── Batch 21 — RAG endpoints ──────────────────────────────────────────────────
@app.get("/rag/stats", dependencies=[Depends(require_api_key)])
async def rag_stats():
    from modules import rag
    return await rag.stats()


@app.get("/rag/search", dependencies=[Depends(require_api_key)])
async def rag_search(q: str, k: int = 5, min_score: float = 0.0):
    from modules import rag
    if not q or not q.strip():
        raise HTTPException(status_code=400, detail="q required")
    hits = await rag.search(q, k=max(1, min(k, 25)), min_score=min_score)
    return {"q": q, "k": k, "hits": hits, "count": len(hits)}


@app.get("/rag/answer", dependencies=[Depends(require_api_key)])
async def rag_answer(q: str, k: int = 3, min_score: float = 1.0):
    from modules import rag
    if not q or not q.strip():
        raise HTTPException(status_code=400, detail="q required")
    res = await rag.answer(q, k=max(1, min(k, 10)), min_score=min_score)
    if res is None:
        return {"q": q, "answer": None, "citations": []}
    return {"q": q, **res}


@app.post("/rag/reindex", dependencies=[Depends(require_api_key)])
async def rag_reindex():
    from modules import rag
    s = await rag.build_index()
    return {"ok": True, "stats": s}


# ── Batch 22 — Observability endpoints ────────────────────────────────────────
@app.get("/metrics")
async def metrics_prometheus():
    """Prometheus text exposition (unauthenticated for scraping; bind 127.0.0.1)."""
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse(obs.render_prometheus(), media_type="text/plain; version=0.0.4")


@app.get("/metrics/json", dependencies=[Depends(require_api_key)])
async def metrics_json():
    return obs.snapshot()


@app.get("/observability/errors", dependencies=[Depends(require_api_key)])
async def observability_errors(limit: int = 50):
    rows = await fetch_all(
        "SELECT module, error_type, message, count, first_seen, last_seen "
        "FROM fazle_error_log ORDER BY last_seen DESC LIMIT $1",
        max(1, min(limit, 500)),
    )
    out = []
    for r in rows:
        out.append({
            "module": r["module"],
            "error_type": r["error_type"],
            "message": r["message"],
            "count": int(r["count"] or 0),
            "first_seen": r["first_seen"].isoformat() if r["first_seen"] else None,
            "last_seen": r["last_seen"].isoformat() if r["last_seen"] else None,
        })
    return {"rows": out, "count": len(out)}


@app.get("/observability/summary", dependencies=[Depends(require_api_key)])
async def observability_summary():
    snap = obs.snapshot()
    # rollup http counters
    http_total = 0
    by_status: dict[str, int] = {}
    by_path: dict[str, int] = {}
    for entry in snap["counters"].get("fazle_http_requests_total", []):
        http_total += int(entry["value"])
        st = entry["labels"].get("status", "0")
        by_status[st] = by_status.get(st, 0) + int(entry["value"])
        p = entry["labels"].get("path", "?")
        by_path[p] = by_path.get(p, 0) + int(entry["value"])
    # latency overall
    durs = snap["histograms"].get("fazle_http_request_duration_ms", [])
    total_count = sum(d["count"] for d in durs)
    total_sum = sum(d["sum_ms"] for d in durs)
    avg_ms = round(total_sum / total_count, 2) if total_count else 0
    # error log totals (last 24h)
    err_24h = 0
    try:
        err_24h = int(await fetch_val(
            "SELECT COALESCE(SUM(count),0) FROM fazle_error_log WHERE last_seen > now() - interval '24 hours'"
        ) or 0)
    except Exception:
        pass
    top_paths = sorted(by_path.items(), key=lambda x: -x[1])[:10]
    return {
        "uptime_s": snap["uptime_s"],
        "http_total": http_total,
        "http_by_status": by_status,
        "http_avg_ms": avg_ms,
        "top_paths": [{"path": p, "count": c} for p, c in top_paths],
        "errors_24h": err_24h,
    }
