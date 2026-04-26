"""
Fazle Core — Escort Lifecycle (Batch 13)

Closes the loop:
  release intent (text or image OCR)
    → find_active_program_for_employee()
    → close_program()         (idempotent: status='Completed')
    → backfill_attendance_for_program()
    → create_escort_payment_draft()  (Batch 12 bridge)
    → admin notification dict

All operations idempotent. Re-running on a closed program returns
{already_closed:True} without creating duplicate drafts.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta
from typing import Optional

from app.database import fetch_one, fetch_all, execute, fetch_val
from modules.payment_workflow import create_escort_payment_draft

log = logging.getLogger("fazle.escort_lifecycle")

# ── Release intent detection ──────────────────────────────────────────────────

RELEASE_KEYWORDS_BN = [
    "ডিউটি শেষ", "ডিউটি বন্ধ", "রিলিজ", "রিলিজ হয়েছি", "ছুটি দিন",
    "পেমেন্ট দেন", "অফ দিন", "শেষ হয়েছে", "ফিরে এসেছি",
    "কাজ শেষ", "ভেসেল ছেড়েছি", "প্রোগ্রাম শেষ",
]
RELEASE_KEYWORDS_EN = [
    "release", "released", "duty done", "duty finished", "duty completed",
    "off duty", "back home", "program completed", "vessel done",
]
# These are keywords that override is_advance_request (which is broader)
_ALL_RELEASE = [k.lower() for k in RELEASE_KEYWORDS_BN + RELEASE_KEYWORDS_EN]


def is_release_intent(text: str) -> bool:
    if not text:
        return False
    t = text.lower()
    return any(kw in t for kw in _ALL_RELEASE)


# ── Program lookup ────────────────────────────────────────────────────────────

async def find_active_program_for_employee(
    employee_id: int,
    on_or_before: Optional[date] = None,
) -> Optional[dict]:
    """Latest non-completed program for this employee whose program_date <= ref_date."""
    ref = on_or_before or date.today()
    row = await fetch_one(
        """SELECT program_id, mother_vessel, lighter_vessel, escort_employee_id,
                  escort_mobile, program_date, shift, status, start_date, end_date,
                  end_shift, release_point, day_count, conveyance, destination
           FROM wbom_escort_programs
           WHERE escort_employee_id = $1
             AND COALESCE(status,'') NOT IN ('Completed','Cancelled')
             AND program_date <= $2
           ORDER BY program_date DESC, program_id DESC
           LIMIT 1""",
        employee_id, ref,
    )
    return dict(row) if row else None


async def find_existing_draft_for_program(program_id: int) -> Optional[dict]:
    row = await fetch_one(
        """SELECT id, employee_id, expected_amount, status, accountant_msg
           FROM fazle_payment_drafts
           WHERE escort_program_id = $1
           ORDER BY id DESC LIMIT 1""",
        program_id,
    )
    return dict(row) if row else None


# ── Close program (idempotent) ────────────────────────────────────────────────

async def close_program(
    program_id: int,
    end_date_v: date,
    end_shift: str,
    release_point: Optional[str],
    day_count: Optional[float],
    completed_by: str,
) -> dict:
    """UPDATE only when status<>'Completed'. Return {ok, already_closed, day_count, program_id}."""
    cur = await fetch_one(
        "SELECT program_id, status, program_date, end_date, day_count "
        "FROM wbom_escort_programs WHERE program_id=$1",
        program_id,
    )
    if not cur:
        return {"ok": False, "error": f"program {program_id} not found"}
    if (cur["status"] or "").lower() == "completed":
        log.info(f"[escort-lifecycle] program {program_id} already Completed")
        return {
            "ok": True, "already_closed": True,
            "program_id": program_id,
            "day_count": float(cur["day_count"] or 0),
        }

    # Compute day_count if missing
    if day_count is None:
        start = cur.get("program_date")
        if start and end_date_v:
            d = (end_date_v - start).days + 1
            day_count = float(max(d, 1))
        else:
            day_count = 1.0

    end_shift = (end_shift or "D").upper()[:1]
    if end_shift not in ("D", "N"):
        end_shift = "D"

    await execute(
        """UPDATE wbom_escort_programs
           SET status='Completed',
               completion_time=NOW(),
               end_date=$2,
               end_shift=$3,
               release_point=COALESCE($4, release_point),
               day_count=$5,
               remarks=COALESCE(remarks,'') || ' | b13-closed-by:' || $6
           WHERE program_id=$1""",
        program_id, end_date_v, end_shift, release_point, day_count, completed_by,
    )
    log.info(f"[escort-lifecycle] closed program {program_id} days={day_count} by={completed_by}")
    return {
        "ok": True, "already_closed": False,
        "program_id": program_id, "day_count": float(day_count),
    }


# ── Attendance backfill ───────────────────────────────────────────────────────

async def backfill_attendance_for_program(program_id: int) -> int:
    """INSERT wbom_attendance rows for program_date..end_date inclusive, ON CONFLICT DO NOTHING."""
    prog = await fetch_one(
        """SELECT escort_employee_id, program_date, end_date, mother_vessel
           FROM wbom_escort_programs WHERE program_id=$1""",
        program_id,
    )
    if not prog or not prog["escort_employee_id"]:
        return 0
    start = prog["program_date"]
    end = prog["end_date"] or start
    if not start:
        return 0
    if end < start:
        end = start
    eid = prog["escort_employee_id"]
    location = prog.get("mother_vessel") or "Escort Duty"
    inserted = 0
    cur = start
    while cur <= end:
        result = await execute(
            """INSERT INTO wbom_attendance
                  (employee_id, attendance_date, status, location, recorded_by)
               VALUES ($1, $2, 'Present', $3, 'escort-lifecycle')
               ON CONFLICT (employee_id, attendance_date) DO NOTHING""",
            eid, cur, location,
        )
        # asyncpg execute returns 'INSERT 0 1' or 'INSERT 0 0'
        if isinstance(result, str) and result.endswith(" 1"):
            inserted += 1
        cur += timedelta(days=1)
    log.info(f"[escort-lifecycle] backfilled {inserted} attendance rows for program {program_id}")
    return inserted


# ── Date parsing helper ───────────────────────────────────────────────────────

_DATE_RES = [
    re.compile(r"\b(20\d{2})-(\d{1,2})-(\d{1,2})\b"),
    re.compile(r"\b(\d{1,2})/(\d{1,2})/(20\d{2})\b"),
    re.compile(r"\b(\d{1,2})-(\d{1,2})-(20\d{2})\b"),
]


def _parse_date(val) -> Optional[date]:
    if isinstance(val, date):
        return val
    if isinstance(val, datetime):
        return val.date()
    if not isinstance(val, str):
        return None
    s = val.strip()
    for pat in _DATE_RES:
        m = pat.search(s)
        if not m:
            continue
        try:
            g = m.groups()
            if len(g[0]) == 4:
                return date(int(g[0]), int(g[1]), int(g[2]))
            return date(int(g[2]), int(g[1]), int(g[0]))
        except Exception:
            continue
    return None


# ── Orchestrator ──────────────────────────────────────────────────────────────

async def handle_release_event(
    employee_id: int,
    extracted: Optional[dict] = None,
    source: str = "release-text",
) -> dict:
    """
    Top-level: close active program, backfill attendance, create payment draft.
    Idempotent: returns existing draft if program already closed.
    """
    extracted = extracted or {}
    prog = await find_active_program_for_employee(employee_id)
    if not prog:
        return {"ok": False, "status": "no_active_program",
                "message": "No assigned/in-progress program found"}

    program_id = int(prog["program_id"])
    end_date_v = _parse_date(extracted.get("end_date")) or date.today()
    end_shift = (extracted.get("end_shift") or extracted.get("shift") or "D")
    release_point = extracted.get("release_point") or extracted.get("location")
    day_count = extracted.get("day_count") or extracted.get("days")
    if day_count is not None:
        try:
            day_count = float(day_count)
        except Exception:
            day_count = None

    closed = await close_program(
        program_id=program_id,
        end_date_v=end_date_v,
        end_shift=end_shift,
        release_point=release_point,
        day_count=day_count,
        completed_by=source,
    )
    if not closed.get("ok"):
        return {"ok": False, "status": "close_failed", **closed}

    inserted_att = await backfill_attendance_for_program(program_id)

    # Idempotency: if already closed, look up existing draft
    if closed.get("already_closed"):
        existing = await find_existing_draft_for_program(program_id)
        return {
            "ok": True, "status": "already_closed",
            "program_id": program_id,
            "day_count": closed.get("day_count"),
            "attendance_inserted": inserted_att,
            "draft_id": existing["id"] if existing else None,
            "existing_draft": existing,
        }

    # Create payment draft via Batch 12 bridge
    draft = await create_escort_payment_draft(
        employee_id=employee_id,
        escort_program_id=program_id,
        override_days=closed.get("day_count"),
        source=source,
    )

    return {
        "ok": True,
        "status": "closed",
        "program_id": program_id,
        "day_count": closed.get("day_count"),
        "attendance_inserted": inserted_att,
        "draft_id": draft.get("draft_id"),
        "draft_text": draft.get("draft_text"),
        "employee_name": draft.get("employee_name"),
        "error": draft.get("error"),
    }
