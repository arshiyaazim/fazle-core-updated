"""
Fazle Core — OCR Image Processor (Phase 4D)

Calls the local media-processor service (port 8090) to extract text from images.
Then parses the OCR text to detect slip type and extract structured fields.

Supported slip types:
  escort_slip    — escort/security duty assignment
  release_slip   — end of duty / release form
  payment_slip   — salary/advance payment record
  unknown        — unrecognized document

Flow:
  file_path → POST /ocr → raw text → classify slip → extract fields → return
"""

import logging
import re
import httpx
from typing import TypedDict, Optional

from app.config import get_settings
from modules.image_hash import check_and_register

log = logging.getLogger("fazle.ocr")


class OcrResult(TypedDict):
    raw_text: str
    slip_type: str          # escort_slip | release_slip | payment_slip | unknown
    is_duplicate: bool
    duplicate_of: Optional[int]

    # Parsed fields (best-effort)
    employee_name: Optional[str]
    employee_id: Optional[str]
    date: Optional[str]
    vessel: Optional[str]
    location: Optional[str]
    amount: Optional[str]
    client: Optional[str]
    reference_no: Optional[str]

    # Suggested reply
    reply: str


async def process_image(file_path: str, message_id: Optional[int] = None) -> OcrResult:
    """
    Full pipeline: hash check → OCR → classify → parse → reply.
    """
    settings = get_settings()

    # Duplicate check
    hash_result = await check_and_register(file_path, message_id)
    if hash_result["is_duplicate"]:
        log.info(f"[ocr] Duplicate image detected for {file_path}")
        return OcrResult(
            raw_text="",
            slip_type="duplicate",
            is_duplicate=True,
            duplicate_of=hash_result["duplicate_of_message_id"],
            employee_name=None, employee_id=None, date=None,
            vessel=None, location=None, amount=None, client=None, reference_no=None,
            reply="এই স্লিপটি পূর্বে জমা হয়েছে। যাচাই চলছে।",
        )

    # OCR call
    raw_text = await _call_ocr(settings.media_processor_url, file_path)
    if not raw_text:
        return OcrResult(
            raw_text="",
            slip_type="unknown",
            is_duplicate=False,
            duplicate_of=None,
            employee_name=None, employee_id=None, date=None,
            vessel=None, location=None, amount=None, client=None, reference_no=None,
            reply="ছবিটি পড়া সম্ভব হয়নি। স্পষ্ট ছবি পাঠান অথবা টেক্সট আকারে তথ্য দিন।",
        )

    # Classify and parse
    slip_type = _classify_slip(raw_text)
    fields = _extract_fields(raw_text, slip_type)
    reply = _build_reply(slip_type, fields)

    log.info(f"[ocr] slip_type={slip_type} name={fields.get('employee_name')} ref={fields.get('reference_no')}")

    return OcrResult(
        raw_text=raw_text,
        slip_type=slip_type,
        is_duplicate=False,
        duplicate_of=None,
        reply=reply,
        **fields,
    )


async def _call_ocr(base_url: str, file_path: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                f"{base_url.rstrip('/')}/ocr",
                json={"file_path": file_path},
            )
            if r.status_code == 200:
                return r.json().get("text", "").strip()
            log.warning(f"[ocr] media-processor returned {r.status_code}")
    except Exception as e:
        log.error(f"[ocr] call error: {e}")
    return ""


def _classify_slip(text: str) -> str:
    t = text.lower()
    escort_kw = ["escort", "এস্কর্ট", "vessel", "ভেসেল", "mother vessel", "lighter",
                 "assignment", "নিয়োগ", "duty slip", "ডিউটি স্লিপ", "escort slip"]
    release_kw = ["release", "রিলিজ", "completion", "সমাপ্ত", "discharge", "cleared"]
    payment_kw = ["payment", "পেমেন্ট", "salary", "বেতন", "advance", "অ্যাডভান্স",
                  "paid", "পরিশোধ", "bkash", "বিকাশ", "nagad", "নগদ", "amount", "টাকা"]

    escort_score = sum(1 for k in escort_kw if k in t)
    release_score = sum(1 for k in release_kw if k in t)
    payment_score = sum(1 for k in payment_kw if k in t)

    best = max(escort_score, release_score, payment_score)
    if best == 0:
        return "unknown"
    if escort_score == best:
        return "escort_slip"
    if release_score == best:
        return "release_slip"
    return "payment_slip"


def _extract_fields(text: str, slip_type: str) -> dict:
    fields = {
        "employee_name": None,
        "employee_id": None,
        "date": None,
        "vessel": None,
        "location": None,
        "amount": None,
        "client": None,
        "reference_no": None,
    }

    lines = [l.strip() for l in text.splitlines() if l.strip()]

    # Date: common formats
    date_match = re.search(
        r"\b(\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|\d{4}-\d{2}-\d{2})\b", text
    )
    if date_match:
        fields["date"] = date_match.group(1)

    # Amount: digits with ৳ or Tk
    amount_match = re.search(r"[৳\bTk\.?]\s*(\d[\d,]+)", text, re.IGNORECASE)
    if amount_match:
        fields["amount"] = amount_match.group(1).replace(",", "")
    else:
        # Standalone number that looks like money
        amt = re.search(r"\b(\d{3,6})\b", text)
        if amt:
            fields["amount"] = amt.group(1)

    # Reference number
    ref = re.search(r"(?:ref|sl|no|#|number)[.:\s#]*([A-Z0-9/-]{4,15})", text, re.IGNORECASE)
    if ref:
        fields["reference_no"] = ref.group(1)

    # Vessel name (for escort slips)
    vessel = re.search(
        r"(?:vessel|ভেসেল|mother vessel|lighter)[:\s]+([A-Za-z0-9\s]{3,30})", text, re.IGNORECASE
    )
    if vessel:
        fields["vessel"] = vessel.group(1).strip()

    # Location / destination
    loc = re.search(
        r"(?:destination|location|port|বন্দর|গন্তব্য)[:\s]+([A-Za-z\s]{3,25})", text, re.IGNORECASE
    )
    if loc:
        fields["location"] = loc.group(1).strip()

    # Name heuristic: look for "Name:" or "নাম:" prefix
    name_match = re.search(r"(?:name|নাম)[:\s]+([A-Za-z\u0980-\u09FF\s.]{3,30})", text, re.IGNORECASE)
    if name_match:
        fields["employee_name"] = name_match.group(1).strip()

    # Employee ID / IC No
    id_match = re.search(r"(?:id|ic|emp)[.:\s#]*(\d{2,6})", text, re.IGNORECASE)
    if id_match:
        fields["employee_id"] = id_match.group(1)

    # Client / company name
    client_match = re.search(
        r"(?:client|company|company name|ক্লায়েন্ট)[:\s]+([A-Za-z\u0980-\u09FF\s&.]{3,40})",
        text, re.IGNORECASE,
    )
    if client_match:
        fields["client"] = client_match.group(1).strip()

    return fields


def _build_reply(slip_type: str, fields: dict) -> str:
    name = fields.get("employee_name") or ""
    date = fields.get("date") or ""
    vessel = fields.get("vessel") or ""
    amount = fields.get("amount") or ""
    ref = fields.get("reference_no") or ""

    if slip_type == "escort_slip":
        parts = ["স্লিপটি পাওয়া গেছে এবং প্রক্রিয়া করা হচ্ছে।"]
        if vessel:
            parts.append(f"ভেসেল: {vessel}।")
        if date:
            parts.append(f"তারিখ: {date}।")
        if name:
            parts.append(f"কর্মী: {name}।")
        return " ".join(parts)

    if slip_type == "release_slip":
        parts = ["রিলিজ স্লিপটি পাওয়া গেছে।"]
        if date:
            parts.append(f"তারিখ: {date}।")
        if name:
            parts.append(f"কর্মী: {name}।")
        return " ".join(parts)

    if slip_type == "payment_slip":
        parts = ["পেমেন্ট স্লিপটি পাওয়া গেছে।"]
        if amount:
            parts.append(f"পরিমাণ: ৳{amount}।")
        if date:
            parts.append(f"তারিখ: {date}।")
        if ref:
            parts.append(f"রেফারেন্স: {ref}।")
        return " ".join(parts)

    return "ডকুমেন্টটি পাওয়া গেছে। তবে ধরনটি চিহ্নিত করা যায়নি। অফিসে যোগাযোগ করুন।"
