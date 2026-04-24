"""
Fazle Core — Admin Command Processor
Parses and executes admin commands received via WhatsApp.

Command formats (case-insensitive):
  APPROVE <id>                    — approve draft reply AND send to recipient
  REJECT <id>                     — reject draft
  EDIT <id> <new text>            — edit draft text
  PAID <id> <amount> <method>     — mark payment as paid and notify accountant
  ADVANCE <id> <amount> <method>  — approve advance payment and notify accountant
  STATUS                          — show pending drafts count
  DRAFTS                          — list recent pending drafts

APPROVE completes the full loop: load draft → mark approved → deliver to recipient
via the correct bridge → mark sent_at. This fires even in SAFE MODE — admin approval
IS the decision to send.
"""
import logging
import re
from typing import Optional

from app.database import fetch_one, fetch_all, execute, fetch_val
from app.config import get_settings

log = logging.getLogger("fazle.admin_cmd")

# ── Command patterns ───────────────────────────────────────────────────────────
_APPROVE_RE  = re.compile(r"^approve\s+(\d+)$", re.IGNORECASE)
_REJECT_RE   = re.compile(r"^reject\s+(\d+)$", re.IGNORECASE)
_EDIT_RE     = re.compile(r"^edit\s+(\d+)\s+(.+)$", re.IGNORECASE | re.DOTALL)
_PAID_RE     = re.compile(r"^paid\s+(\d+)\s+(\d[\d,]*)\s*(bkash|nagad|cash)?$", re.IGNORECASE)
_ADVANCE_RE  = re.compile(r"^advance\s+(\d+)\s+(\d[\d,]*)\s*(bkash|nagad|cash)?$", re.IGNORECASE)
_STATUS_RE   = re.compile(r"^(status|drafts|pending)$", re.IGNORECASE)


def is_admin_command(text: str) -> bool:
    """Quick check if message is an admin command."""
    t = text.strip()
    return bool(
        _APPROVE_RE.match(t) or _REJECT_RE.match(t) or _EDIT_RE.match(t) or
        _PAID_RE.match(t) or _ADVANCE_RE.match(t) or _STATUS_RE.match(t)
    )


async def process_admin_command(text: str, admin_phone: str) -> str:
    """
    Parse and execute admin command. Returns confirmation text to send back to admin.
    NEVER sends to external parties — only returns text.
    Calling code decides whether to forward to accountant etc.
    """
    t = text.strip()

    m = _APPROVE_RE.match(t)
    if m:
        return await _cmd_approve(int(m.group(1)), admin_phone)

    m = _REJECT_RE.match(t)
    if m:
        return await _cmd_reject(int(m.group(1)), admin_phone)

    m = _EDIT_RE.match(t)
    if m:
        return await _cmd_edit(int(m.group(1)), m.group(2).strip(), admin_phone)

    m = _PAID_RE.match(t)
    if m:
        draft_id = int(m.group(1))
        amount   = float(m.group(2).replace(",", ""))
        method   = (m.group(3) or "cash").lower()
        return await _cmd_paid(draft_id, amount, method, admin_phone, draft_type="escort_payment")

    m = _ADVANCE_RE.match(t)
    if m:
        draft_id = int(m.group(1))
        amount   = float(m.group(2).replace(",", ""))
        method   = (m.group(3) or "cash").lower()
        return await _cmd_paid(draft_id, amount, method, admin_phone, draft_type="advance")

    if _STATUS_RE.match(t):
        return await _cmd_status()

    return "❌ অজানা কমান্ড। সাহায্যের জন্য: APPROVE <id> / REJECT <id> / PAID <id> <amount> <method>"


# ── Command implementations ────────────────────────────────────────────────────

async def _cmd_approve(draft_id: int, admin_phone: str) -> str:
    """
    Approve a draft reply — mark approved then SEND to recipient immediately.
    Admin approval IS the decision to send, regardless of AUTO_REPLY_ENABLED.
    """
    try:
        row = await fetch_one(
            "SELECT * FROM fazle_draft_replies WHERE id = $1", draft_id
        )
        if not row:
            return f"❌ Draft #{draft_id} পাওয়া যায়নি।"
        if row.get("status") not in ("pending", None, "edited"):
            return f"⚠️ Draft #{draft_id} ইতিমধ্যে {row.get('status', 'processed')}।"

        recipient = row.get("recipient", "")
        reply_text = row.get("reply_text", "")
        source = row.get("source", "bridge1")

        # Mark approved first
        await execute(
            "UPDATE fazle_draft_replies SET status='approved', admin_phone=$1, approved_at=NOW() WHERE id=$2",
            admin_phone, draft_id,
        )

        # Attempt to deliver — import bridges here to avoid circular imports
        sent = False
        error_text = ""
        try:
            from app.bridge import get_bridge1, get_bridge2
            bridge = get_bridge1() if source in ("bridge1", "meta") else get_bridge2()
            # Bridge expects JID format for non-meta sends
            jid = recipient if "@" in recipient else f"{recipient}@s.whatsapp.net"
            sent = await bridge.send(jid, reply_text)
            if sent:
                await execute(
                    "UPDATE fazle_draft_replies SET status='sent', sent_at=NOW() WHERE id=$1",
                    draft_id,
                )
                log.info(f"[admin_cmd] Draft #{draft_id} sent to {recipient} via {source}")
            else:
                error_text = "Bridge send returned false"
                await execute(
                    "UPDATE fazle_draft_replies SET error_text=$1 WHERE id=$2",
                    error_text, draft_id,
                )
        except Exception as send_err:
            error_text = str(send_err)[:200]
            log.error(f"[admin_cmd] Draft #{draft_id} send error: {send_err}")
            await execute(
                "UPDATE fazle_draft_replies SET error_text=$1 WHERE id=$2",
                error_text, draft_id,
            )

        preview = reply_text[:200] if reply_text else ""
        if sent:
            return (
                f"✅ Draft #{draft_id} অনুমোদিত ও পাঠানো হয়েছে।\n\n"
                f"প্রাপক: {recipient}\n"
                f"বার্তা:\n{preview}"
            )
        else:
            return (
                f"✅ Draft #{draft_id} অনুমোদিত — কিন্তু পাঠাতে সমস্যা হয়েছে।\n"
                f"ত্রুটি: {error_text}\n\n"
                f"প্রাপক: {recipient}\n"
                f"বার্তা:\n{preview}"
            )
    except Exception as e:
        log.error(f"[admin_cmd] approve error: {e}")
        return f"❌ ত্রুটি: {e}"


async def _cmd_reject(draft_id: int, admin_phone: str) -> str:
    """Reject a draft reply."""
    try:
        row = await fetch_one(
            "SELECT id, status FROM fazle_draft_replies WHERE id = $1", draft_id
        )
        if not row:
            return f"❌ Draft #{draft_id} পাওয়া যায়নি।"
        if row.get("status") in ("sent", "rejected"):
            return f"⚠️ Draft #{draft_id} ইতিমধ্যে {row.get('status')}।"

        await execute(
            "UPDATE fazle_draft_replies SET status='rejected', admin_phone=$1, approved_at=NOW() WHERE id=$2",
            admin_phone, draft_id,
        )
        return f"🚫 Draft #{draft_id} বাতিল করা হয়েছে।"
    except Exception as e:
        log.error(f"[admin_cmd] reject error: {e}")
        return f"❌ ত্রুটি: {e}"


async def _cmd_edit(draft_id: int, new_text: str, admin_phone: str) -> str:
    """Edit draft reply text."""
    try:
        row = await fetch_one(
            "SELECT id FROM fazle_draft_replies WHERE id = $1", draft_id
        )
        if not row:
            return f"❌ Draft #{draft_id} পাওয়া যায়নি।"

        await execute(
            "UPDATE fazle_draft_replies SET reply_text=$1, status='edited', admin_phone=$2 WHERE id=$3",
            new_text, admin_phone, draft_id,
        )
        return f"✏️ Draft #{draft_id} আপডেট করা হয়েছে।\n\nনতুন বার্তা:\n{new_text[:300]}"
    except Exception as e:
        log.error(f"[admin_cmd] edit error: {e}")
        return f"❌ ত্রুটি: {e}"


async def _cmd_paid(
    draft_id: int, amount: float, method: str, admin_phone: str, draft_type: str
) -> tuple[str, Optional[str]]:
    """
    Approve payment draft and build accountant message.
    Returns (admin_confirm_text, accountant_message).
    Stored as tuple but callers can treat as plain str (first element).
    """
    try:
        row = await fetch_one(
            "SELECT * FROM fazle_payment_drafts WHERE id = $1", draft_id
        )
        if not row:
            # Try draft_replies as fallback
            return (f"❌ Payment draft #{draft_id} পাওয়া যায়নি।", None)

        emp_name   = row.get("employee_name") or "?"
        emp_mobile = row.get("employee_phone") or "?"
        method_code = {"bkash": "B", "nagad": "N", "cash": "C"}.get(method, "C")

        accountant_msg = (
            f"💳 পেমেন্ট নির্দেশনা:\n\n"
            f"কর্মী: {emp_name}\n"
            f"মোবাইল: {emp_mobile}\n"
            f"পরিমাণ: ৳{amount:,.0f}\n"
            f"পদ্ধতি: {method.upper()} ({method_code})\n"
            f"ধরন: {'অগ্রিম' if draft_type == 'advance' else 'এস্কর্ট ডিউটি পেমেন্ট'}\n"
            f"Draft ID: #{draft_id}"
        )

        await execute(
            """UPDATE fazle_payment_drafts
               SET status='approved', approved_amount=$1, payment_method=$2,
                   admin_phone=$3, accountant_msg=$4, updated_at=NOW()
               WHERE id=$5""",
            amount, method, admin_phone, accountant_msg, draft_id,
        )

        confirm = (
            f"✅ Payment #{draft_id} অনুমোদিত।\n"
            f"কর্মী: {emp_name} | পরিমাণ: ৳{amount:,.0f} | পদ্ধতি: {method.upper()}\n\n"
            f"একাউন্ট্যান্টকে পেমেন্ট বার্তা পাঠানো হচ্ছে..."
        )
        # Store accountant_msg in the tuple slot for the caller
        return (confirm, accountant_msg)

    except Exception as e:
        log.error(f"[admin_cmd] paid error: {e}")
        return (f"❌ ত্রুটি: {e}", None)


async def _cmd_status() -> str:
    """Return pending drafts count."""
    try:
        draft_count = await fetch_val(
            "SELECT COUNT(*) FROM fazle_draft_replies WHERE COALESCE(status,'pending') = 'pending'"
        )
        pay_count = await fetch_val(
            "SELECT COUNT(*) FROM fazle_payment_drafts WHERE status='pending'"
        )
        rows = await fetch_all(
            "SELECT id, recipient, intent, LEFT(reply_text,60) AS preview FROM fazle_draft_replies "
            "WHERE COALESCE(status,'pending') = 'pending' ORDER BY created_at DESC LIMIT 5"
        )
        lines = [f"📊 পেন্ডিং ড্রাফট: {draft_count} | পেমেন্ট ড্রাফট: {pay_count}\n"]
        for r in rows:
            lines.append(f"#{r['id']} [{r.get('intent','?')}] → {r.get('recipient','?')}: {r.get('preview','')!r}")
        return "\n".join(lines)
    except Exception as e:
        return f"❌ স্ট্যাটাস লোড ব্যর্থ: {e}"


async def list_payment_drafts(admin_phone: str) -> str:
    """List pending payment drafts for admin."""
    try:
        rows = await fetch_all(
            "SELECT id, employee_name, employee_phone, amount, method, notes "
            "FROM fazle_payment_drafts WHERE status='pending' ORDER BY created_at DESC LIMIT 10"
        )
        if not rows:
            return "✅ কোনো পেন্ডিং পেমেন্ট ড্রাফট নেই।"
        lines = ["💳 পেন্ডিং পেমেন্ট ড্রাফটসমূহ:\n"]
        for r in rows:
            method_label = r.get('method') or 'cash'
            lines.append(
                f"#{r['id']} {r.get('employee_name','?')} "
                f"| ৳{r.get('amount') or 0:,.0f} "
                f"| {method_label}"
            )
        lines.append("\nঅনুমোদন দিতে: PAID <id> <amount> bkash/nagad/cash")
        return "\n".join(lines)
    except Exception as e:
        return f"❌ ত্রুটি: {e}"
