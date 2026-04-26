"""
Fazle Core — Vessel Escort & Client Order Processing

FLOW:
  1. escort_client sends message
       → extract MV / lighter(s) / master mobile
       → save to wbom_escort_programs (status='draft', extras in remarks JSON)
       → build admin draft(s)
       → return ("", admin_note)   ← NO reply to client
  2. Admin fills escort name/mobile, sends completed draft back
       → system detects completed draft
       → looks up original client from DB remarks
       → sends finalized slip to client via admin_note routing
       → replies to admin with delivery confirmation
       → updates DB status to 'confirmed'

DB usage (no schema change):
  wbom_escort_programs columns used:
    mother_vessel, lighter_vessel, master_mobile,
    destination, status, contact_id, program_date, remarks
  remarks stores JSON:
    {sender_phone, source_bridge, escort_name, escort_mobile,
     capacity, importer, cargo_type}
"""

import json
import logging
import re
from datetime import date
from typing import Optional, TypedDict

from app.database import execute, fetch_one, fetch_val

log = logging.getLogger("fazle.escort")


# ── TypedDicts ─────────────────────────────────────────────────────────────────

class LighterInfo(TypedDict):
    lighter_vessel: str
    master_name: Optional[str]
    master_mobile: Optional[str]
    capacity: Optional[str]
    destination: Optional[str]


class EscortOrder(TypedDict):
    mother_vessel: Optional[str]
    importer: Optional[str]
    cargo_type: Optional[str]
    lighters: list           # list[LighterInfo]
    date_hint: Optional[str]
    raw_text: str


class CompletedDraft(TypedDict):
    mother_vessel: Optional[str]
    lighter_vessel: Optional[str]
    master_mobile: Optional[str]
    escort_name: Optional[str]
    escort_mobile: Optional[str]
    date_str: Optional[str]
    shift: Optional[str]     # "D" or "N"


# ── Compiled patterns ──────────────────────────────────────────────────────────

_MOBILE_RE = re.compile(r"\b((?:880|0)1[3-9]\d{8})\b")

_MV_LABEL_RE = re.compile(
    r"(?:m\.?v\.?|এমভি|mother\s*vessel)\s*[:\s.]*"
    r"([A-Za-z][A-Za-z0-9\s.\-]{1,40}?)(?=\s*[\n/]|$)",
    re.IGNORECASE | re.MULTILINE,
)
_IMPORTER_RE = re.compile(
    r"(?:a/?c\.?\s*|account\s*:?\s*)\*?([A-Za-z][\w\s]{2,25}?)(?=\s*[\n$]|$)",
    re.IGNORECASE | re.MULTILINE,
)
_CARGO_RE = re.compile(
    r"\b(wheat|corn|soybean|soya(?:bean)?\s*meal|coal|sugar|rice|salt|"
    r"গম|ভুট্টা|চিনি|কয়লা)\b",
    re.IGNORECASE,
)
_CAPACITY_RE = re.compile(r"(\d[\d,]+)\s*m\.?t\.?", re.IGNORECASE)
_DATE_RE = re.compile(r"\b(\d{1,2}[./-]\d{1,2}(?:[./-]\d{2,4})?)\b")

# Completed draft detection
_CD_ESCORT_NAME_RE = re.compile(
    r"escort\s*(?:name)?\s*:\s*([A-Za-zঀ-৿][A-Za-zঀ-৿\s]{1,20})",
    re.IGNORECASE,
)
_CD_ESCORT_MOB_RE = re.compile(
    r"escort\s*(?:mobile|mob|number)?\s*:\s*(\b(?:880|0)1[3-9]\d{8}\b)",
    re.IGNORECASE,
)
_CD_SHIFT_RE = re.compile(r"\(\s*([DN])\s*\)", re.IGNORECASE)
_CD_MV_RE = re.compile(
    r"(?:m\.?v\.?|mother\s*vessel)\s*[:\s.]*([A-Za-z][A-Za-z0-9\s.\-]{1,40}?)(?=\s*\n|$)",
    re.IGNORECASE | re.MULTILINE,
)
_CD_LV_RE = re.compile(
    r"(?:lighter(?:\s*vessel)?|lv)\s*:\s*([A-Za-z0-9][A-Za-z0-9\s.\-]{1,35}?)(?=\s*\n|$)",
    re.IGNORECASE | re.MULTILINE,
)
_CD_MASTER_MOB_RE = re.compile(
    r"(?:master(?:'?s)?\s*(?:number|mobile|mob)?|mob)\s*:\s*(\b(?:880|0)1[3-9]\d{8}\b)",
    re.IGNORECASE,
)
_AL_AQSA_RE = re.compile(r"al.?aqsa|আল.?আকসা", re.IGNORECASE)


# ── Extraction helpers ─────────────────────────────────────────────────────────

def _extract_mother_vessel(text: str) -> Optional[str]:
    m = _MV_LABEL_RE.search(text)
    if not m:
        return None
    name = m.group(1).strip().rstrip(".,- ")
    if name.upper().startswith("MV "):
        return name.upper()
    return f"MV {name.upper()}"


def _parse_lighter_block(section: str) -> Optional[LighterInfo]:
    """Parse one labeled 'Lighter: ...' block."""
    lines = section.strip().splitlines()
    if not lines:
        return None

    # First line: lighter name (strip the "Lighter[: ...]" label)
    lighter_name = re.sub(
        r"^lighter\s*(?:vessel\s*)?:?\s*", "", lines[0], flags=re.IGNORECASE
    ).strip()
    if not lighter_name:
        return None

    rest = "\n".join(lines[1:])

    master_name_m = re.search(
        r"master\s*:?\s*(.+?)(?=\s*\n|mob|$)", rest, re.IGNORECASE | re.MULTILINE
    )
    master_name = master_name_m.group(1).strip() if master_name_m else None

    mob_m = _MOBILE_RE.search(rest)
    master_mobile = mob_m.group(1) if mob_m else None

    cap_m = _CAPACITY_RE.search(rest)
    capacity = f"{cap_m.group(1)} MT" if cap_m else None

    dest_m = re.search(
        r"(?:dest(?:ination)?|going\s*to)\s*:?\s*(.+?)(?=\s*\n|$)",
        rest, re.IGNORECASE | re.MULTILINE,
    )
    destination = dest_m.group(1).strip() if dest_m else None

    return LighterInfo(
        lighter_vessel=lighter_name,
        master_name=master_name,
        master_mobile=master_mobile,
        capacity=capacity,
        destination=destination,
    )


def _parse_labeled_lighters(text: str) -> list:
    """Split on 'Lighter:' or 'Lighter Vessel:' labels and parse each block."""
    # Split keeping the delimiter in each segment
    parts = re.split(r"(?=\blighter\s*(?:vessel\s*)?:)", text, flags=re.IGNORECASE)
    lighters = []
    for part in parts:
        if re.match(r"lighter\s*(?:vessel\s*)?:", part, re.IGNORECASE):
            li = _parse_lighter_block(part)
            if li:
                lighters.append(li)
    return lighters


def _parse_inline_lighters(text: str) -> list:
    """
    Handle compact format: '1.LighterName-01712345678 Destination 700 MT'
    or bare 'LighterName 01712345678 Destination capacity' lines.
    """
    lighters = []

    # Numbered format: digit. Name-mobile rest
    numbered = re.compile(
        r"^\d+\.\s*([A-Za-z][A-Za-z0-9\s]{1,30}?)\s*[-–]\s*"
        r"(\b(?:880|0)1[3-9]\d{8}\b)\s*(.+)?$",
        re.MULTILINE,
    )
    for m in numbered.finditer(text):
        name = m.group(1).strip()
        mobile = m.group(2)
        rest = (m.group(3) or "").strip()
        cap_m = _CAPACITY_RE.search(rest)
        capacity = f"{cap_m.group(1)} MT" if cap_m else None
        dest = re.sub(_CAPACITY_RE, "", rest).strip() or None
        lighters.append(LighterInfo(
            lighter_vessel=name,
            master_name=None,
            master_mobile=mobile,
            capacity=capacity,
            destination=dest,
        ))

    if lighters:
        return lighters

    # Last resort: any mobile number on same line as a vessel-like word
    for m in _MOBILE_RE.finditer(text):
        line_start = text.rfind("\n", 0, m.start()) + 1
        line_end = text.find("\n", m.end())
        if line_end == -1:
            line_end = len(text)
        line = text[line_start:line_end]

        before = line[: m.start() - line_start].strip()
        after = line[m.end() - line_start :].strip()

        # Skip if "before" looks like a label (master, mob, escort…)
        if re.match(r"(master|mob|mobile|escort|phone|tel)", before, re.IGNORECASE):
            continue
        if before:
            cap_m = _CAPACITY_RE.search(after)
            lighters.append(LighterInfo(
                lighter_vessel=before,
                master_name=None,
                master_mobile=m.group(1),
                capacity=f"{cap_m.group(1)} MT" if cap_m else None,
                destination=None,
            ))

    return lighters


def parse_escort_message(text: str) -> EscortOrder:
    """Extract all vessel/lighter info from a client escort message."""
    mother_vessel = _extract_mother_vessel(text)

    imp_m = _IMPORTER_RE.search(text)
    importer = imp_m.group(1).strip() if imp_m else None

    cargo_m = _CARGO_RE.search(text)
    cargo_type = cargo_m.group(0).strip() if cargo_m else None

    date_m = _DATE_RE.search(text)
    date_hint = date_m.group(1) if date_m else None

    # Try labeled first, then inline
    lighters = _parse_labeled_lighters(text)
    if not lighters:
        lighters = _parse_inline_lighters(text)

    return EscortOrder(
        mother_vessel=mother_vessel,
        importer=importer,
        cargo_type=cargo_type,
        lighters=lighters,
        date_hint=date_hint,
        raw_text=text,
    )


# ── Draft builders ─────────────────────────────────────────────────────────────

_TODAY = lambda: date.today().strftime("%d.%m.%Y")


def build_admin_draft(mv: str, lighter: LighterInfo, date_str: Optional[str] = None) -> str:
    """Build the standardized draft to send to admin for one lighter."""
    d = date_str or _TODAY()
    lines = [
        f"Mother Vessel: {mv or '—'}",
        f"Lighter Vessel: {lighter['lighter_vessel']}",
        f"Master's Number: {lighter['master_mobile'] or '—'}",
        "Escort Name:",
        "Escort Mobile:",
        f"Date: {d} (D/N)",
        "Al-Aqsa Security Service",
    ]
    return "\n".join(lines)


def build_admin_message(order: EscortOrder, sender_phone: str) -> str:
    """Build the full admin message for one escort order (may include multiple lighters)."""
    mv = order["mother_vessel"] or "Unknown MV"
    parts = []

    if order["importer"] or order["cargo_type"]:
        meta = []
        if order["importer"]:
            meta.append(f"A/c: {order['importer']}")
        if order["cargo_type"]:
            meta.append(f"Cargo: {order['cargo_type']}")
        parts.append("📦 " + " | ".join(meta))

    parts.append(f"📱 Client: {sender_phone}")
    parts.append("")

    for i, lighter in enumerate(order["lighters"], 1):
        if len(order["lighters"]) > 1:
            parts.append(f"── Lighter {i} ──")
        parts.append(build_admin_draft(mv, lighter, order.get("date_hint")))
        parts.append("")

    if not order["lighters"]:
        # No lighter extracted — send partial draft for admin to fill
        dummy = LighterInfo(
            lighter_vessel="(লাইটার নাম যোগ করুন)",
            master_name=None,
            master_mobile=None,
            capacity=None,
            destination=None,
        )
        parts.append(build_admin_draft(mv, dummy, order.get("date_hint")))

    parts.append("➤ Escort Name ও Mobile যোগ করে পাঠিয়ে দিন।")
    return "\n".join(parts)


# ── Completed draft detection & parsing ───────────────────────────────────────

def is_completed_escort_draft(text: str) -> bool:
    """Return True if admin message is a filled-in escort draft."""
    has_escort_name = bool(_CD_ESCORT_NAME_RE.search(text))
    has_escort_mob = bool(_CD_ESCORT_MOB_RE.search(text))
    has_al_aqsa = bool(_AL_AQSA_RE.search(text))
    return has_escort_name and has_escort_mob and has_al_aqsa


def parse_completed_draft(text: str) -> CompletedDraft:
    """Extract all fields from admin's completed draft."""
    mv_m = _CD_MV_RE.search(text)
    lv_m = _CD_LV_RE.search(text)
    master_mob_m = _CD_MASTER_MOB_RE.search(text)
    escort_name_m = _CD_ESCORT_NAME_RE.search(text)
    escort_mob_m = _CD_ESCORT_MOB_RE.search(text)
    date_m = _DATE_RE.search(text)
    shift_m = _CD_SHIFT_RE.search(text)

    mv = mv_m.group(1).strip() if mv_m else None
    if mv and not mv.upper().startswith("MV"):
        mv = f"MV {mv}"

    return CompletedDraft(
        mother_vessel=mv,
        lighter_vessel=lv_m.group(1).strip() if lv_m else None,
        master_mobile=master_mob_m.group(1) if master_mob_m else None,
        escort_name=escort_name_m.group(1).strip() if escort_name_m else None,
        escort_mobile=escort_mob_m.group(1) if escort_mob_m else None,
        date_str=date_m.group(1) if date_m else _TODAY(),
        shift=(shift_m.group(1).upper() if shift_m else None),
    )


def build_final_slip(draft: CompletedDraft) -> str:
    """Build the finalized slip to send to the original client."""
    shift_label = f" ({draft['shift']})" if draft.get("shift") else ""
    lines = [
        f"Mother Vessel: {draft['mother_vessel'] or '—'}",
        f"Lighter: {draft['lighter_vessel'] or '—'}",
        f"Master Number: {draft['master_mobile'] or '—'}",
        f"Escort Name: {draft['escort_name'] or '—'}",
        f"Escort Mobile: {draft['escort_mobile'] or '—'}",
        f"{draft['date_str'] or _TODAY()}{shift_label}",
        "Al-Aqsa Security Service",
    ]
    return "\n".join(lines)


# ── DB helpers ─────────────────────────────────────────────────────────────────

async def _get_contact_id(phone: str) -> Optional[int]:
    row = await fetch_one(
        "SELECT contact_id FROM wbom_contacts WHERE whatsapp_number = $1 LIMIT 1",
        phone,
    )
    return row["contact_id"] if row else None


async def save_escort_programs(
    order: EscortOrder,
    sender_phone: str,
    source: str,
) -> list:
    """Save one DB row per lighter (or one partial row if no lighters)."""
    program_ids = []
    mv = order["mother_vessel"] or ""
    contact_id = await _get_contact_id(sender_phone)

    lighters_to_save = order["lighters"] if order["lighters"] else [
        LighterInfo(
            lighter_vessel="",
            master_name=None,
            master_mobile=None,
            capacity=None,
            destination=None,
        )
    ]

    for lighter in lighters_to_save:
        remarks_data = {
            "sender_phone": sender_phone,
            "source_bridge": source,
            "escort_name": None,
            "escort_mobile": None,
            "capacity": lighter.get("capacity"),
            "importer": order.get("importer"),
            "cargo_type": order.get("cargo_type"),
        }
        try:
            row = await fetch_one(
                """
                INSERT INTO wbom_escort_programs (
                    mother_vessel, lighter_vessel, master_mobile,
                    destination, status, contact_id,
                    program_date, shift, remarks
                ) VALUES ($1, $2, $3, $4, 'draft', $5, CURRENT_DATE, 'D', $6)
                RETURNING program_id
                """,
                mv,
                lighter["lighter_vessel"],
                lighter["master_mobile"] or "",
                lighter.get("destination") or "",
                contact_id,
                json.dumps(remarks_data, ensure_ascii=False),
            )
            if row:
                pid = row["program_id"]
                program_ids.append(pid)
                log.info(f"[escort] saved program_id={pid} mv={mv} lv={lighter['lighter_vessel']}")
        except Exception as e:
            log.error(f"[escort] save error: {e}")

    return program_ids


async def _find_pending_program(mv: Optional[str], lv: Optional[str]) -> Optional[dict]:
    """Find most recent 'draft' program matching MV and/or lighter name."""
    mv_clean = (mv or "").upper().replace("MV ", "").strip()
    lv_clean = (lv or "").strip()

    try:
        if mv_clean and lv_clean:
            row = await fetch_one(
                """SELECT program_id, remarks, mother_vessel, lighter_vessel
                   FROM wbom_escort_programs
                   WHERE UPPER(REPLACE(mother_vessel, 'MV ', '')) = $1
                     AND LOWER(lighter_vessel) = LOWER($2)
                     AND status = 'draft'
                   ORDER BY program_date DESC LIMIT 1""",
                mv_clean, lv_clean,
            )
            if row:
                return dict(row)

        if mv_clean:
            row = await fetch_one(
                """SELECT program_id, remarks, mother_vessel, lighter_vessel
                   FROM wbom_escort_programs
                   WHERE UPPER(REPLACE(mother_vessel, 'MV ', '')) = $1
                     AND status = 'draft'
                   ORDER BY program_date DESC LIMIT 1""",
                mv_clean,
            )
            if row:
                return dict(row)
    except Exception as e:
        log.error(f"[escort] find program error: {e}")
    return None


async def _update_program_confirmed(
    program_id: int,
    escort_name: str,
    escort_mobile: str,
) -> bool:
    try:
        await execute(
            """UPDATE wbom_escort_programs
               SET status = 'confirmed',
                   remarks = remarks::jsonb
                           || jsonb_build_object(
                               'escort_name', $2::text,
                               'escort_mobile', $3::text
                           )
               WHERE program_id = $1""",
            program_id, escort_name, escort_mobile,
        )
        return True
    except Exception as e:
        log.error(f"[escort] update confirmed error: {e}")
        return False


# ── Public entry points ────────────────────────────────────────────────────────

async def handle_escort_client_message(
    text: str,
    sender_phone: str,
    source: str,
) -> tuple[str, Optional[dict]]:
    """
    Entry point for messages from escort_client role.
    Extracts vessel data, saves to DB, sends draft to admin.
    Returns ("", admin_note) — no reply to client.
    """
    from modules.message_router import get_primary_admin

    order = parse_escort_message(text)
    log.info(
        f"[escort] client={sender_phone} mv={order['mother_vessel']} "
        f"lighters={len(order['lighters'])}"
    )

    await save_escort_programs(order, sender_phone, source)

    admin_msg = build_admin_message(order, sender_phone)
    admin_phone = get_primary_admin()

    if not admin_phone:
        log.warning("[escort] no admin phone configured")
        return "", None

    return "", {
        "admin_phone": admin_phone,
        "text": f"🚢 নতুন এস্কর্ট অর্ডার:\n\n{admin_msg}",
        "bridge": source,
    }


async def handle_admin_escort_completion(
    text: str,
    admin_phone: str,
    source: str,
) -> tuple[str, Optional[dict]]:
    """
    Entry point for admin's completed escort draft.
    Finds original client, sends final slip to them, confirms to admin.
    """
    draft = parse_completed_draft(text)
    log.info(
        f"[escort] admin completion: mv={draft['mother_vessel']} "
        f"lv={draft['lighter_vessel']} escort={draft['escort_name']}"
    )

    program = await _find_pending_program(draft["mother_vessel"], draft["lighter_vessel"])

    if not program:
        return (
            "⚠️ মিলানো পেন্ডিং অর্ডার পাওয়া যায়নি। "
            "MV / Lighter নাম যাচাই করুন।",
            None,
        )

    # Parse extras from remarks JSON
    try:
        remarks = json.loads(program.get("remarks") or "{}")
    except (json.JSONDecodeError, TypeError):
        remarks = {}

    client_phone = remarks.get("sender_phone")
    client_bridge = remarks.get("source_bridge", source)

    # Update DB
    await _update_program_confirmed(
        program["program_id"],
        draft["escort_name"] or "",
        draft["escort_mobile"] or "",
    )

    final_slip = build_final_slip(draft)

    if not client_phone:
        # No client phone stored — only confirm to admin
        return (
            f"✅ DB আপডেট হয়েছে।\n"
            f"⚠️ ক্লায়েন্ট নম্বর পাওয়া যায়নি — নিজে পাঠিয়ে দিন।\n\n"
            f"{final_slip}",
            None,
        )

    # Send final slip to original client via admin_note routing
    admin_confirm = (
        f"✅ পাঠানো হয়েছে → {client_phone}\n\n"
        f"📋 Slip:\n{final_slip}"
    )

    return admin_confirm, {
        "admin_phone": client_phone,
        "text": final_slip,
        "bridge": client_bridge,
    }


# ── Legacy compatibility shim (called by old message_router path) ──────────────

async def handle_escort_order(
    text: str,
    sender_phone: str,
    source: str,
) -> tuple[str, bool]:
    """
    Kept for backward compatibility with any old call sites.
    Delegates to handle_escort_client_message — returns (reply, complete).
    """
    reply, _ = await handle_escort_client_message(text, sender_phone, source)
    return reply, False
