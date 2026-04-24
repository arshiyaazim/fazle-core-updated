"""
Fazle Core — Unified Message Router

Single source of truth for all inbound message routing.
Imported by both app/main.py (webhook path) and modules/bridge_poller (SQLite path).

Returns: (reply_text, admin_notification | None)
  admin_notification = {"admin_phone": str, "text": str, "bridge": str}

Routing priority:
  1. escort_client role → extract vessel data, draft to admin, no client reply
  2. Admin: completed escort draft → finalize, send to client
  3. Admin: commands (APPROVE / REJECT / PAID / ADVANCE / list)
  4. Recruitment funnel (active session → KB → trigger detection)
  5. Escort intent fallback (unregistered senders with escort content)
  6. Employee: verification session → slip/advance/salary
  7. Knowledge base (all roles, before LLM)
  8. AI fallback (Ollama)
"""

import logging
from typing import Optional

from app.config import get_settings
from app.database import fetch_one, fetch_all
from app import ollama as ai
from modules.intent import classify
from modules.user_role import detect_role
from modules.payroll_logic import get_payroll_summary, format_payroll_context
from modules.escort import (
    handle_escort_client_message,
    handle_admin_escort_completion,
    is_completed_escort_draft,
)
from modules.knowledge_base import get_reply as kb_get_reply
from modules.recruitment_flow import (
    intake_message as recruit_intake,
    get_active_session,
    is_recruitment_trigger,
)
from modules.admin_commands import is_admin_command, process_admin_command, list_payment_drafts
from modules.payment_workflow import is_advance_request
from modules.employee_verification import (
    get_verification_session,
    advance_verification,
    start_advance_verification,
    start_slip_verification,
    check_identity_mismatch,
)

log = logging.getLogger("fazle.router")


async def process_message(
    sender: str, text: str, source: str
) -> tuple[str, Optional[dict]]:
    """
    Route one inbound message and return (reply_text, admin_notification | None).
    Does NOT send anything — callers handle delivery.
    """
    settings = get_settings()

    user_role = await detect_role(sender)
    role_str = user_role["role"]
    log.info(f"[ROLE] {sender} → {role_str}")

    # ── 1. ESCORT CLIENT — never reply, always draft to admin ─────────────────
    if role_str == "escort_client":
        return await handle_escort_client_message(text, sender, source)

    # ── 2. ADMIN ───────────────────────────────────────────────────────────────
    if role_str == "admin":
        # Check for completed escort draft before command parsing
        if is_completed_escort_draft(text):
            return await handle_admin_escort_completion(text, sender, source)

        if is_admin_command(text):
            result = await process_admin_command(text, sender)
            if isinstance(result, tuple):
                confirm_text, accountant_msg = result
                if accountant_msg:
                    accountant_phone = settings.accountant_phone
                    return confirm_text, {
                        "admin_phone": accountant_phone,
                        "text": accountant_msg,
                        "bridge": source,
                    }
                return confirm_text, None
            return result, None  # type: ignore[return-value]

        lower = text.lower()
        if "draft" in lower or "পেন্ডিং" in lower or "list" in lower:
            return await _cmd_admin_list(), None
        if "payment" in lower or "পেমেন্ট" in lower or "paid" in lower:
            return await list_payment_drafts(sender), None

    # ── 2. Intent classification ───────────────────────────────────────────────
    intent = classify(text)
    if intent == "unknown":
        intent = await ai.classify_intent_llm(text)
    log.info(f"[INTENT] {sender} → {intent}")

    # ── 3. RECRUITMENT ─────────────────────────────────────────────────────────
    if role_str in ("new_lead", "known_contact") or intent == "recruitment":
        active_session = await get_active_session(sender)
        if active_session:
            result = await recruit_intake(sender, text, source)
            if result["reply"]:
                return result["reply"], None

        kb_reply = await kb_get_reply(text, intent)
        if kb_reply:
            return kb_reply, None

        if is_recruitment_trigger(text):
            result = await recruit_intake(sender, text, source)
            if result["reply"]:
                return result["reply"], None

    # ── 4. ESCORT ORDER (intent-triggered, non-escort_client roles) ────────────
    # escort_client is handled at step 1 — this catches unregistered senders
    # whose message content looks like an escort order (intent-based fallback).
    if intent in ("client_order", "escort_duty"):
        return await handle_escort_client_message(text, sender, source)

    # ── 5. EMPLOYEE ────────────────────────────────────────────────────────────
    if role_str == "employee":
        emp_id = user_role.get("employee_id")

        # Identity mismatch: phone is a vessel master, not a registered employee
        if not emp_id:
            mismatch = await check_identity_mismatch(sender)
            if mismatch:
                return mismatch, None

        # Active verification session takes priority over everything else
        session = await get_verification_session(sender)
        if session:
            return await advance_verification(sender, text, source, emp_id)

        # Release slip / duty completion — start slip verification flow
        if intent == "slip_submission":
            return await start_slip_verification(sender, text, source, emp_id)

        # Advance / emergency / financial assistance request — start verification flow
        if is_advance_request(text):
            return await start_advance_verification(sender, source, emp_id)

        # Salary / payment information query (read-only — no verification needed)
        if intent in ("salary_query", "payment_due") and emp_id:
            payroll = await get_payroll_summary(emp_id)
            db_ctx = format_payroll_context(payroll)
            reply = await ai.generate_reply(text, intent, db_ctx, role=role_str)
            return reply, None

    # ── 6. KNOWLEDGE BASE (all roles, all intents) ─────────────────────────────
    kb_reply = await kb_get_reply(text, intent)
    if kb_reply:
        return kb_reply, None

    # ── 7. AI FALLBACK ─────────────────────────────────────────────────────────
    db_ctx = await get_contact_context(sender)
    reply = await ai.generate_reply(text, intent, db_ctx, role=role_str)
    return reply, None


# ── Helpers ────────────────────────────────────────────────────────────────────

def get_primary_admin() -> str:
    settings = get_settings()
    admins = settings.admin_number_list
    return admins[0] if admins else settings.admin_meta_number


async def get_contact_context(phone: str) -> str:
    lines: list[str] = []
    try:
        phone_variants = [phone]
        if phone.startswith("880") and len(phone) >= 13:
            phone_variants.append("0" + phone[3:])
        elif phone.startswith("01") and len(phone) == 11:
            phone_variants.append("880" + phone[1:])

        contact = None
        for v in phone_variants:
            contact = await fetch_one(
                """SELECT c.display_name, c.company_name, rt.relation_name
                   FROM wbom_contacts c
                   LEFT JOIN wbom_relation_types rt ON rt.relation_type_id = c.relation_type_id
                   WHERE c.whatsapp_number = $1 AND c.is_active = true
                   LIMIT 1""",
                v,
            )
            if contact:
                break

        if contact:
            relation = contact.get("relation_name") or "Contact"
            company = f" ({contact.get('company_name','')})" if contact.get("company_name") else ""
            lines.append(f"{relation}: {contact.get('display_name','?')}{company}")

        emp = None
        for v in phone_variants:
            emp = await fetch_one(
                "SELECT employee_name, designation, basic_salary, status FROM wbom_employees WHERE employee_mobile=$1",
                v,
            )
            if emp:
                break
        if emp:
            lines.append(
                f"কর্মী: {emp['employee_name']}, {emp.get('designation','')}, "
                f"বেতন: ৳{emp['basic_salary']}, স্ট্যাটাস: {emp['status']}"
            )
    except Exception as e:
        log.debug(f"Context fetch error: {e}")
    return "\n".join(lines)


async def _cmd_admin_list() -> str:
    try:
        rows = await fetch_all(
            """SELECT id, recipient, intent, status, LEFT(reply_text, 80) AS preview, created_at
               FROM fazle_draft_replies
               WHERE COALESCE(status, 'pending') = 'pending'
               ORDER BY created_at DESC LIMIT 10"""
        )
        if not rows:
            return "✅ কোনো পেন্ডিং ড্রাফট নেই।"
        lines = [f"📋 পেন্ডিং ড্রাফট ({len(rows)} টি):\n"]
        for r in rows:
            lines.append(
                f"#{r['id']} [{r.get('intent','?')}] → {r.get('recipient','?')}\n"
                f"   {r.get('preview','')!r}"
            )
        lines.append("\nঅনুমোদন: APPROVE <id> | বাতিল: REJECT <id>")
        return "\n".join(lines)
    except Exception as e:
        return f"❌ ড্রাফট লোড ব্যর্থ: {e}"
