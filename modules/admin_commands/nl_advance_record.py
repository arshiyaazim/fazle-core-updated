"""
Fazle Core — Admin NL: direct advance recording (v1.0)

Lets admin record an advance payment in free-form Bangla/English/Banglish.
No draft required — writes directly to wbom_cash_transactions.

Supported patterns (examples):
  "Karim advance দিলাম 5000 bkash"
  "advance দিয়েছি ID 123 / 3000 / nagad"
  "advance record: 01712345678, 2000, cash"
  "অগ্রিম দেওয়া হয়েছে কারিম মিয়া ৫০০০ টাকা"
  "ID 456 advance 2000"

Trigger: (advance|অগ্রিম) + (দিলাম|দিয়েছি|দেওয়া হয়েছে|record) + amount
       OR "record advance" prefix + amount

Does NOT fire on:
  - "ADVANCE 45 5000 bkash"  (structured command — handled upstream)
  - Employee messages saying "advance চাই" / "advance লাগবে"  (not admin)
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from app.database import execute, fetch_one, fetch_val

log = logging.getLogger("fazle.admin_nl_advrec")

# ── Bengali digit normalisation ────────────────────────────────────────────────
_BN_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")

# ── Trigger detection ──────────────────────────────────────────────────────────
# Must contain advance/অগ্রিম AND a "gave/recorded" verb (not "want/need")
_TRIGGER_RE = re.compile(
    r"(?:"
    r"(?:advance|অগ্রিম)\b.{0,80}"
    r"(?:দিলাম|দিয়েছি|দিয়ে দিলাম|দিয়ে দিয়েছি|দেওয়া হয়েছে|দেওয়া হল|করা হয়েছে|record(?:ed)?|paid)"
    r"|(?:record|রেকর্ড)\s+(?:advance|অগ্রিম)"
    r")",
    re.IGNORECASE | re.DOTALL,
)


def is_advance_record_query(text: str) -> bool:
    t = text.strip().translate(_BN_DIGITS)
    # Must NOT look like the structured ADVANCE command (handled upstream)
    if re.match(r"^advance\s+\d+\s+[\d,]+", t, re.IGNORECASE):
        return False
    if not _TRIGGER_RE.search(t):
        return False
    # Must also contain a number (the amount)
    if not re.search(r"\d[\d,]*", t):
        return False
    return True


# ── Parsers ────────────────────────────────────────────────────────────────────
_EMP_ID_RE = re.compile(
    r"(?:id|ID|#)\s*[:\-]?\s*(\d+)"
    r"|কর্মী\s*নম্বর\s*[:\-]?\s*(\d+)",
    re.IGNORECASE,
)
_PHONE_RE = re.compile(r"(?:\+?88)?(01[3-9]\d{8})")
_AMOUNT_RE = re.compile(r"\b(\d[\d,]*)\s*(?:টাকা|taka|৳|tk)?\b")
_METHOD_RE = re.compile(
    r"\b(bkash|বিকাশ|nagad|নগদ|rocket|রকেট|cash|ক্যাশ|নগদ\s*ক্যাশ)\b",
    re.IGNORECASE,
)
_REMARKS_RE = re.compile(
    r"(?:remarks?|note|মন্তব্য|কারণ|বিস্তারিত)\s*[:\-]?\s*(.+?)(?:\n|$)",
    re.IGNORECASE,
)


def _parse_emp_id(text: str) -> Optional[int]:
    m = _EMP_ID_RE.search(text)
    if m:
        return int(m.group(1) or m.group(2))
    return None


def _parse_phone(text: str) -> Optional[str]:
    t = text.translate(_BN_DIGITS)
    m = _PHONE_RE.search(t)
    return ("88" + m.group(1)) if m else None


def _parse_amount(text: str) -> Optional[float]:
    t = text.translate(_BN_DIGITS)
    # Collect all numbers; the largest numeric-only token is most likely the amount
    # (avoids picking up phone number digits)
    matches = _AMOUNT_RE.findall(t)
    candidates: list[float] = []
    for raw in matches:
        val = float(raw.replace(",", ""))
        if 10 <= val <= 1_000_000:  # sanity range for advances
            candidates.append(val)
    return max(candidates) if candidates else None


def _parse_method(text: str) -> str:
    m = _METHOD_RE.search(text)
    if not m:
        return "cash"
    v = m.group(1).lower()
    if "bkash" in v or "বিকাশ" in v:
        return "bkash"
    if "nagad" in v or "নগদ" in v:
        return "nagad"
    if "rocket" in v or "রকেট" in v:
        return "rocket"
    return "cash"


def _parse_remarks(text: str) -> Optional[str]:
    m = _REMARKS_RE.search(text)
    return m.group(1).strip() if m else None


# ── Employee lookup ────────────────────────────────────────────────────────────
async def _lookup_employee(emp_id: Optional[int], phone: Optional[str]) -> Optional[dict]:
    if emp_id:
        row = await fetch_one(
            "SELECT employee_id, employee_name, employee_mobile, designation "
            "FROM wbom_employees WHERE employee_id = $1",
            emp_id,
        )
        if row:
            return dict(row)

    if phone:
        local = phone[2:] if phone.startswith("88") else phone
        row = await fetch_one(
            "SELECT employee_id, employee_name, employee_mobile, designation "
            "FROM wbom_employees "
            "WHERE employee_mobile = $1 OR employee_mobile = $2",
            phone, local,
        )
        if row:
            return dict(row)

    return None


async def _cumulative_advance(employee_id: int) -> float:
    total = await fetch_val(
        "SELECT COALESCE(SUM(amount), 0) FROM wbom_cash_transactions "
        "WHERE employee_id = $1 AND transaction_type = 'advance'",
        employee_id,
    )
    return float(total or 0)


# ── Public handler ─────────────────────────────────────────────────────────────
async def intent_advance_record(text: str, admin_phone: str, whatsapp_message_id: Optional[int] = None) -> str:
    t = text.translate(_BN_DIGITS)

    emp_id = _parse_emp_id(t)
    phone = _parse_phone(t)
    amount = _parse_amount(t)
    method = _parse_method(t)
    remarks = _parse_remarks(t) or f"Admin direct record — {admin_phone}"

    if amount is None:
        return (
            "পরিমাণ বুঝতে পারিনি। উদাহরণ:\n"
            "advance দিলাম ID 123 / 5000 / bkash\n"
            "অগ্রিম দেওয়া হয়েছে 01712345678, 3000 cash"
        )

    emp = await _lookup_employee(emp_id, phone)
    if emp is None:
        hint = f"ID {emp_id}" if emp_id else (phone or "?")
        return (
            f"কর্মী পাওয়া যায়নি ({hint})।\n"
            "সঠিক Employee ID বা মোবাইল নম্বর দিন।"
        )

    method_map = {"bkash": "bKash", "nagad": "Nagad", "rocket": "Rocket", "cash": "Cash"}
    method_display = method_map.get(method, "Cash")

    try:
        await execute(
            """INSERT INTO wbom_cash_transactions
                   (employee_id, amount, transaction_type, payment_method,
                    transaction_date, remarks, employee_phone, source, whatsapp_message_id)
               VALUES ($1, $2, 'advance', $3, CURRENT_DATE, $4, $5, 'admin_nl', $6)""",
            emp["employee_id"], amount, method,
            f"Admin NL: {remarks}",
            emp.get("employee_mobile"),
            whatsapp_message_id,
        )
    except Exception as e:
        log.error(f"[adv_record] DB insert error: {e}")
        return f"DB সংরক্ষণে সমস্যা: {e}"

    cumulative = await _cumulative_advance(emp["employee_id"])

    log.info(
        f"[adv_record] admin={admin_phone} recorded advance: "
        f"emp_id={emp['employee_id']} amount={amount} method={method}"
    )

    return (
        f"অগ্রিম রেকর্ড হয়েছে।\n\n"
        f"কর্মী: {emp['employee_name']}\n"
        f"মোবাইল: {emp.get('employee_mobile', '?')}\n"
        f"পদবি: {emp.get('designation', '?')}\n"
        f"পরিমাণ: ৳{amount:,.0f} ({method_display})\n"
        f"মোট অগ্রিম (সঞ্চিত): ৳{cumulative:,.0f}"
    )
