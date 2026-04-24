"""
Fazle Core — Payment Workflow
Handles:
  1. Escort duty release → payment draft → admin notification
  2. Advance payment request → admin approval draft
  3. Salary finalization (after admin approval)

Source business logic: resources/Cash Payment Accountant-Admin.txt

Flow (Escort Payment):
  Employee sends release slip photo
  → System verifies duty record exists
  → Calculates expected payment (daily_rate × duty_days - advances)
  → Creates payment draft in fazle_payment_drafts
  → Sends draft to admin WhatsApp (suppressed in safe mode)
  → Admin sends PAID <id> <amount> <method>
  → System sends accountant message
  → Records in wbom_cash_transactions

Flow (Advance):
  Employee requests advance
  → System checks recent payments and pending duty
  → Creates advance draft
  → Admin approves with ADVANCE <id> <amount> <method>
"""
import logging
from datetime import date, datetime, timezone
from typing import Optional

from app.database import fetch_one, fetch_all, execute, fetch_val
from app.config import get_settings

log = logging.getLogger("fazle.payment")

# ── Config ─────────────────────────────────────────────────────────────────────
DEFAULT_DAILY_RATE = 1200  # ৳1200/day for escort duty


# ── Escort payment draft ───────────────────────────────────────────────────────

async def create_escort_payment_draft(
    employee_id: int,
    escort_program_id: Optional[int] = None,
    override_days: Optional[float] = None,
) -> dict:
    """
    Create a payment draft after escort duty release.
    Returns draft info including the text to send to admin.
    """
    try:
        emp = await fetch_one(
            """SELECT employee_id, employee_name, employee_mobile, designation,
                      basic_salary, bkash_number, nagad_number
               FROM wbom_employees WHERE employee_id = $1""",
            employee_id,
        )
        if not emp:
            return {"error": f"Employee {employee_id} not found", "draft_id": None}

        duty_days = override_days
        prog_name = "Unknown Program"

        # Try to get program info
        if escort_program_id:
            prog = await fetch_one(
                """SELECT program_id, program_date, vessel_name, release_date,
                          completion_date
                   FROM wbom_escort_programs WHERE program_id = $1""",
                escort_program_id,
            )
            if prog:
                prog_name = prog.get("vessel_name") or f"Program #{escort_program_id}"
                if duty_days is None:
                    # Calculate from dates
                    start = prog.get("program_date")
                    end   = prog.get("release_date") or prog.get("completion_date")
                    if start and end:
                        if hasattr(start, "date"):
                            start = start.date()
                        if hasattr(end, "date"):
                            end = end.date()
                        delta = (end - start).days + 1
                        duty_days = max(float(delta), 1.0)

        if duty_days is None:
            duty_days = 1.0

        # Calculate payable
        daily_rate = float(emp.get("basic_salary") or DEFAULT_DAILY_RATE) / 30
        expected   = round(duty_days * daily_rate, 0)

        # Check previous advances
        advances = await fetch_val(
            """SELECT COALESCE(SUM(amount), 0)
               FROM wbom_cash_transactions
               WHERE employee_id = $1 AND transaction_type = 'advance'
                 AND created_at >= NOW() - INTERVAL '60 days'""",
            employee_id,
        ) or 0.0
        net_payable = max(float(expected) - float(advances), 0)

        bkash = emp.get("bkash_number") or emp.get("nagad_number") or "?"

        draft_text = (
            f"💼 এস্কর্ট পেমেন্ট রিকোয়েস্ট:\n\n"
            f"কর্মী: {emp['employee_name']}\n"
            f"ডিউটি: {prog_name}\n"
            f"দিন: {duty_days:.1f}\n"
            f"প্রত্যাশিত: ৳{expected:,.0f}\n"
            f"অগ্রিম কর্তন: ৳{advances:,.0f}\n"
            f"নেট দেয়: ৳{net_payable:,.0f}\n"
            f"বিকাশ/নগদ: {bkash}\n\n"
            f"✅ অনুমোদন দিতে: PAID <draft_id> {net_payable:.0f} bkash"
        )

        # Save draft
        draft_id = await fetch_val(
            """INSERT INTO fazle_payment_drafts
                   (draft_type, employee_id, employee_name, employee_mobile,
                    escort_program_id, duty_days, expected_amount, status, draft_text,
                    created_at, updated_at)
               VALUES ('escort_payment', $1, $2, $3, $4, $5, $6, 'pending', $7, NOW(), NOW())
               RETURNING id""",
            employee_id, emp["employee_name"], emp.get("employee_mobile"),
            escort_program_id, duty_days, net_payable, draft_text,
        )

        if draft_id:
            # Update draft_text with actual ID
            draft_text = draft_text.replace("<draft_id>", str(draft_id))
            await execute(
                "UPDATE fazle_payment_drafts SET draft_text=$1 WHERE id=$2",
                draft_text, draft_id,
            )

        log.info(f"[payment] escort draft #{draft_id} created for emp {employee_id}, ৳{net_payable:,.0f}")
        return {
            "draft_id": draft_id,
            "draft_text": draft_text,
            "employee_name": emp["employee_name"],
            "expected_amount": net_payable,
            "duty_days": duty_days,
        }

    except Exception as e:
        log.error(f"[payment] create_escort_payment_draft error: {e}")
        return {"error": str(e), "draft_id": None}


# ── Advance payment draft ──────────────────────────────────────────────────────

async def create_advance_request_draft(
    employee_id: int,
    requested_amount: Optional[float] = None,
) -> dict:
    """
    Create an advance payment approval draft for admin.
    """
    try:
        emp = await fetch_one(
            """SELECT employee_id, employee_name, employee_mobile,
                      basic_salary, bkash_number, nagad_number
               FROM wbom_employees WHERE employee_id = $1""",
            employee_id,
        )
        if not emp:
            return {"error": f"Employee {employee_id} not found", "draft_id": None}

        # Recent payments this month
        month_start = date.today().replace(day=1)
        paid_this_month = await fetch_val(
            """SELECT COALESCE(SUM(amount), 0)
               FROM wbom_cash_transactions
               WHERE employee_id = $1 AND created_at >= $2""",
            employee_id, month_start,
        ) or 0.0

        # Active duties count
        active_duties = await fetch_val(
            """SELECT COUNT(*)
               FROM wbom_escort_programs
               WHERE escort_employee_id = $1 AND status = 'active'""",
            employee_id,
        ) or 0

        bkash = emp.get("bkash_number") or emp.get("nagad_number") or "?"
        amount_str = f"৳{requested_amount:,.0f}" if requested_amount else "অনির্দিষ্ট"

        draft_text = (
            f"💰 অগ্রিম পেমেন্ট রিকোয়েস্ট:\n\n"
            f"কর্মী: {emp['employee_name']}\n"
            f"মোবাইল: {emp.get('employee_mobile','?')}\n"
            f"চাওয়া পরিমাণ: {amount_str}\n"
            f"এই মাসে পেয়েছে: ৳{paid_this_month:,.0f}\n"
            f"সক্রিয় ডিউটি: {active_duties}\n"
            f"বিকাশ/নগদ: {bkash}\n\n"
            f"✅ অনুমোদন দিতে: ADVANCE <draft_id> <পরিমাণ> bkash/nagad/cash\n"
            f"🚫 বাতিল করতে: REJECT <draft_id>"
        )

        draft_id = await fetch_val(
            """INSERT INTO fazle_payment_drafts
                   (draft_type, employee_id, employee_name, employee_mobile,
                    expected_amount, status, draft_text, created_at, updated_at)
               VALUES ('advance', $1, $2, $3, $4, 'pending', $5, NOW(), NOW())
               RETURNING id""",
            employee_id, emp["employee_name"], emp.get("employee_mobile"),
            requested_amount or 0, draft_text,
        )

        if draft_id:
            draft_text = draft_text.replace("<draft_id>", str(draft_id))
            await execute(
                "UPDATE fazle_payment_drafts SET draft_text=$1 WHERE id=$2",
                draft_text, draft_id,
            )

        log.info(f"[payment] advance draft #{draft_id} for emp {employee_id}")
        return {
            "draft_id": draft_id,
            "draft_text": draft_text,
            "employee_name": emp["employee_name"],
        }

    except Exception as e:
        log.error(f"[payment] advance_request_draft error: {e}")
        return {"error": str(e), "draft_id": None}


# ── Finalize payment (after admin approves) ────────────────────────────────────

async def finalize_payment(draft_id: int, approved_amount: float, method: str) -> dict:
    """
    Record payment in wbom_cash_transactions after admin approval.
    Returns accountant_message to forward to accountant.
    """
    try:
        draft = await fetch_one(
            "SELECT * FROM fazle_payment_drafts WHERE id = $1", draft_id
        )
        if not draft:
            return {"error": f"Draft #{draft_id} not found"}

        method_map = {"bkash": "Bkash", "nagad": "Nagad", "cash": "Cash"}
        method_display = method_map.get(method.lower(), method.upper())

        # Record transaction
        txn_type = "advance" if draft.get("draft_type") == "advance" else "escort_payment"
        await execute(
            """INSERT INTO wbom_cash_transactions
                   (employee_id, amount, transaction_type, payment_method,
                    notes, created_at)
               VALUES ($1, $2, $3, $4, $5, NOW())""",
            draft.get("employee_id"), approved_amount, txn_type, method,
            f"Draft #{draft_id} — approved by admin",
        )

        accountant_msg = (
            f"💳 পেমেন্ট নির্দেশনা\n\n"
            f"কর্মী: {draft.get('employee_name','?')}\n"
            f"মোবাইল: {draft.get('employee_mobile','?')}\n"
            f"পরিমাণ: ৳{approved_amount:,.0f}\n"
            f"পদ্ধতি: {method_display}\n"
            f"ধরন: {'অগ্রিম' if txn_type == 'advance' else 'এস্কর্ট পেমেন্ট'}\n"
            f"Draft: #{draft_id}"
        )

        # Mark draft as sent
        await execute(
            """UPDATE fazle_payment_drafts
               SET status='sent', accountant_msg=$1, updated_at=NOW()
               WHERE id=$2""",
            accountant_msg, draft_id,
        )

        log.info(f"[payment] finalized draft #{draft_id}: ৳{approved_amount:,.0f} via {method}")
        return {
            "accountant_msg": accountant_msg,
            "employee_name": draft.get("employee_name"),
            "amount": approved_amount,
            "method": method_display,
        }

    except Exception as e:
        log.error(f"[payment] finalize error: {e}")
        return {"error": str(e)}


# ── Employee advance request detector ─────────────────────────────────────────

ADVANCE_KEYWORDS = [
    # Core advance / salary
    "অগ্রিম", "অগ্রীম", "advance", "আগাম",
    "টাকা দেন", "টাকা লাগবে", "টাকা দরকার",
    "পেমেন্ট দেন", "বেতন দেন",
    # Emergency / personal crisis
    "ইমার্জেন্সি", "জরুরি টাকা", "জরুরী টাকা",
    "চিকিৎসার জন্য", "হাসপাতালে", "হাসপাতাল",
    "পরিবারের জন্য", "পরিবার সংকট",
    "বিপদে পড়েছি", "বিপদ", "সংকট",
    # Help / assistance
    "সাহায্য করুন", "সাহায্য লাগবে", "হেল্প",
]


def is_advance_request(text: str) -> bool:
    """Check if employee message is an advance / emergency payment request."""
    t = text.lower()
    return any(kw in t for kw in ADVANCE_KEYWORDS)
