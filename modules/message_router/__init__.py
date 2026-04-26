"""
Fazle Core — Unified Message Router

Single source of truth for all inbound message routing.
Imported by both app/main.py (webhook path) and modules/bridge_poller (SQLite path).

Returns: (reply_text, admin_notification | None)
  admin_notification = {"admin_phone": str, "text": str, "bridge": str}

Routing priority:
  1. family         → personal safe reply, no business workflow
  2. escort roles   → extract vessel data, draft to admin, no client reply
  3. admin          → commands (APPROVE / REJECT / PAID / ADVANCE / ESCORTCONFIRM / list)
  4. supervisor     → attendance check (attendance_parser) → then KB/AI
  5. accountant     → finance route (KB, then AI)
  6. candidate      → recruitment funnel
  7. employee       → verification → slip/advance/salary/attendance
  8. known contacts (repeat_client / vendor / vip_client) → KB → AI
  9. unknown        → intent engine → KB → AI
"""

import logging
from typing import Optional

from app.config import get_settings
from app.database import fetch_one, fetch_all
from app import ollama as ai
from modules.intent import classify
from modules.identity_brain import detect_identity
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
from modules.attendance import handle_attendance_message, is_attendance_message, get_attendance_summary
from modules.attendance_parser import (
    parse_attendance, create_attendance_draft, save_supervisor_attendance,
    is_supervisor_attendance,
)
from modules.employee_verification import (
    get_verification_session,
    advance_verification,
    start_advance_verification,
    start_slip_verification,
    check_identity_mismatch,
)

log = logging.getLogger("fazle.router")

# Roles that trigger the escort client flow
_ESCORT_ROLES = frozenset({"escort_client", "client_escort_buyer", "vip_client", "repeat_client"})


async def process_message(
    sender: str, text: str, source: str
) -> tuple[str, Optional[dict]]:
    """
    Route one inbound message and return (reply_text, admin_notification | None).
    Does NOT send anything — callers handle delivery.
    """
    settings = get_settings()

    identity = await detect_identity(sender, text)
    role_str = identity["role"]
    log.info(
        f"[IDENTITY] {sender} → {role_str} "
        f"(conf={identity['identity_confidence']}, src={identity['identity_source']})"
    )

    # ── 1. FAMILY — personal, no business workflow ────────────────────────────
    if role_str == "family":
        name = identity.get("display_name") or "আপনি"
        return (
            f"আস-সালামু আলাইকুম {name}! 😊\n"
            f"এটা অফিসের নম্বর — ব্যক্তিগত কথা ফোনে বলুন।",
            None,
        )

    # ── 2. ESCORT CLIENT roles — never reply, always draft to admin ───────────
    if role_str in _ESCORT_ROLES:
        # Only trigger escort flow if message has escort content
        intent_check = classify(text)
        if intent_check in ("client_order", "escort_duty") or _looks_like_escort_order(text):
            return await handle_escort_client_message(text, sender, source)
        # Otherwise fall through to normal routing below

    # ── 3. ADMIN ───────────────────────────────────────────────────────────────
    if role_str == "admin":
        if is_completed_escort_draft(text):
            return await handle_admin_escort_completion(text, sender, source)

        if is_admin_command(text):
            result = await process_admin_command(text, sender)
            if isinstance(result, tuple):
                confirm_text, extra_msg = result
                if extra_msg and len(result) == 2:
                    # Could be accountant_msg or buyer_msg — check context
                    # process_admin_command now returns (confirm, msg_to_forward)
                    # Route to accountant for PAID/ADVANCE; to buyer for ESCORTCONFIRM
                    target_phone = _resolve_forward_target(text, settings)
                    if target_phone and extra_msg:
                        return confirm_text, {
                            "admin_phone": target_phone,
                            "text": extra_msg,
                            "bridge": source,
                        }
                return confirm_text, None
            return result, None  # type: ignore[return-value]

        lower = text.lower()
        if "draft" in lower or "পেন্ডিং" in lower or "list" in lower:
            return await _cmd_admin_list(), None
        if "payment" in lower or "পেমেন্ট" in lower or "paid" in lower:
            return await list_payment_drafts(sender), None
        if "attendance" in lower or "হাজিরা" in lower or "উপস্থিতি" in lower:
            return await get_attendance_summary(), None

    # ── 4. ATTENDANCE (any role) — draft for admin approval ───────────────────
    # Checks supervisor AND any sender — admin approval required before DB save
    if is_supervisor_attendance(text) or (role_str != "admin" and is_attendance_message(text)):
        parsed = parse_attendance(text)
        result = await create_attendance_draft(parsed, sender, source)
        admin_phone = settings.admin_bridge1_number if source == "bridge1" \
            else settings.admin_bridge2_number
        return result["message"], {
            "admin_phone": admin_phone,
            "text": result["admin_msg"],
            "bridge": source,
        }

    # ── 5. Intent classification ───────────────────────────────────────────────
    intent = classify(text)
    if intent == "unknown":
        intent = await ai.classify_intent_llm(text)
    log.info(f"[INTENT] {sender} → {intent}")

    # ── 6. ACCOUNTANT ─────────────────────────────────────────────────────────
    if role_str == "accountant":
        kb_reply = await kb_get_reply(text, intent)
        if kb_reply:
            return kb_reply, None
        db_ctx = await get_contact_context(sender)
        reply = await ai.generate_reply(text, intent, db_ctx, role=role_str)
        return reply, None

    # ── 7. CANDIDATE — recruitment funnel ─────────────────────────────────────
    if role_str == "candidate" or intent == "recruitment":
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

    # ── 8. RECRUITMENT for new_lead / known_contact roles ─────────────────────
    if role_str in ("new_lead", "unknown", "known_contact"):
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

    # ── 9. ESCORT ORDER (intent-triggered for non-registered senders) ─────────
    if intent in ("client_order", "escort_duty"):
        return await handle_escort_client_message(text, sender, source)

    # ── 10. EMPLOYEE ───────────────────────────────────────────────────────────
    if role_str == "employee":
        emp_id = identity.get("employee_id")

        if not emp_id:
            mismatch = await check_identity_mismatch(sender)
            if mismatch:
                return mismatch, None

        session = await get_verification_session(sender)
        if session:
            return await advance_verification(sender, text, source, emp_id)

        if intent == "attendance" or is_attendance_message(text):
            return await handle_attendance_message(text, sender, source)

        if intent == "slip_submission":
            return await start_slip_verification(sender, text, source, emp_id)

        # Batch 13: text-based release intent (close active program, draft payment)
        if emp_id:
            from modules.escort_lifecycle import is_release_intent, handle_release_event
            if is_release_intent(text):
                rel = await handle_release_event(emp_id, extracted=None, source=f"release-text:{source}")
                if rel.get("status") == "closed":
                    reply = (
                        f"✅ আপনার ডিউটি বন্ধের তথ্য পাওয়া গেছে।\n"
                        f"Days: {rel.get('day_count')}\n"
                        f"পেমেন্ট ড্রাফট তৈরি হয়েছে (#{rel.get('draft_id')})।\n"
                        f"Admin অনুমোদনের পর পেমেন্ট প্রসেস হবে।"
                    )
                    return reply, None
                if rel.get("status") == "already_closed":
                    return ("ℹ️ আপনার সর্বশেষ ডিউটি ইতিমধ্যে ক্লোজ করা হয়েছে। "
                            "আবার রিলিজ পাঠানোর প্রয়োজন নেই।"), None
                if rel.get("status") == "no_active_program":
                    return ("⚠️ আপনার নামে কোনো চলমান escort program পাওয়া যায়নি। "
                            "Admin-কে যোগাযোগ করুন।"), None

        if is_advance_request(text):
            return await start_advance_verification(sender, source, emp_id)

        if intent in ("salary_query", "payment_due") and emp_id:
            payroll = await get_payroll_summary(emp_id)
            db_ctx = format_payroll_context(payroll)
            reply = await ai.generate_reply(text, intent, db_ctx, role=role_str)
            return reply, None

    # ── 11. ADVANCE/PAYMENT REQUEST (any role not already handled) ───────────
    # Catches employees, supervisors, unknown senders who ask for money
    existing_session = await get_verification_session(sender)
    if not existing_session and is_advance_request(text) and role_str != "admin":
        return await start_advance_verification(sender, source, identity.get("employee_id"))

    # ── 12. KNOWLEDGE BASE (all roles, all intents) ───────────────────────────
    kb_reply = await kb_get_reply(text, intent)
    if kb_reply:
        return kb_reply, None

    # ── 13. AI FALLBACK ────────────────────────────────────────────────────────
    db_ctx = await get_contact_context(sender)
    reply = await ai.generate_reply(text, intent, db_ctx, role=role_str)
    return reply, None


# ── Helpers ────────────────────────────────────────────────────────────────────

def get_primary_admin() -> str:
    settings = get_settings()
    admins = settings.admin_number_list
    return admins[0] if admins else settings.admin_meta_number


def _looks_like_escort_order(text: str) -> bool:
    """Check if message contains escort order keywords."""
    import re
    pattern = re.compile(
        r"\b(m\.?v\.?|mother\s*vessel|lighter|escort\s*lagbe|m\.?t\.|"
        r"এমভি|destination|lighter\s*vessel|master\s*number)\b",
        re.IGNORECASE,
    )
    return bool(pattern.search(text))


def _resolve_forward_target(command_text: str, settings) -> Optional[str]:
    """Determine who to forward the secondary message to based on command type."""
    t = command_text.strip().lower()
    if t.startswith(("paid", "advance")):
        return settings.accountant_phone or None
    if t.startswith("escortconfirm"):
        # Buyer phone is embedded in the result from _cmd_escort_confirm
        # The escort confirm handler already returns None if no buyer found
        # Forward via the admin_note mechanism in the caller
        return None  # handled by the tuple itself
    return None


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
