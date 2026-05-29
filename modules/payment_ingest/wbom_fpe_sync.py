"""
Fazle Core — WBOM → FPE Sync Adapter  (Phase 2 / Phase 8)
===========================================================

Bridges WBOM cash transactions into the FPE canonical pipeline so that:
  - The payroll dashboard reads from a single source (fpe_cash_transactions)
  - WBOM tables are NEVER mutated or deleted
  - Duplicate rows are never created (idempotent via existing txn_ref check)

USAGE
-----
    from modules.payment_ingest.wbom_fpe_sync import sync_wbom_transaction

    # Called after a WBOM cash transaction is finalized:
    await sync_wbom_transaction(wbom_txn_id=1234)

    # Or backfill a date range:
    from modules.payment_ingest.wbom_fpe_sync import backfill_wbom_to_fpe
    synced, skipped = await backfill_wbom_to_fpe(since_days=30)

This module is ADDITIVE — it never touches wbom_* tables.

Source: 11-phase architectural plan 2026-05
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple

from app.database import fetch_one, fetch_all, execute, fetch_val
from modules.fazle_payroll_engine.employee import match_or_create_employee
from modules.fazle_payroll_engine.accounting import create_transaction
from shared.phone import normalize_bd_phone

log = logging.getLogger("fazle.wbom_fpe_sync")

# Canonical source label stored in fpe_wa_messages / fpe_cash_transactions
_SOURCE_WBOM = "wbom"

# Method normalization: WBOM uses mixed-case strings; FPE expects lowercase enum
_METHOD_MAP: dict[str, str] = {
    "bkash": "bkash",
    "bKash": "bkash",
    "nagad": "nagad",
    "Nagad": "nagad",
    "cash": "cash",
    "Cash": "cash",
    "rocket": "rocket",
    "Rocket": "rocket",
    "bank": "bank",
}


def _norm_method(raw: Optional[str]) -> str:
    if not raw:
        return "cash"
    return _METHOD_MAP.get(raw.strip(), raw.strip().lower())


async def sync_wbom_transaction(wbom_txn_id: int) -> Optional[dict]:
    """
    Read one row from wbom_cash_transactions and create a matching row in
    fpe_cash_transactions if one does not already exist.

    Returns the FPE transaction dict (new or existing), or None on error.
    Never raises.
    """
    try:
        wbom = await fetch_one(
            """
            SELECT ct.transaction_id, ct.employee_id, ct.amount, ct.transaction_type,
                   ct.transaction_date, ct.payment_method, ct.reference_number,
                   ct.remarks, ct.created_at,
                   e.employee_name, e.employee_mobile, e.bkash_number, e.nagad_number
            FROM   wbom_cash_transactions ct
            JOIN   wbom_employees e ON e.employee_id = ct.employee_id
            WHERE  ct.transaction_id = $1
            """,
            wbom_txn_id,
        )
        if not wbom:
            log.warning("[wbom_fpe_sync] wbom_txn_id=%d not found", wbom_txn_id)
            return None

        # Determine the best phone for the employee
        raw_phone = (
            wbom.get("bkash_number")
            or wbom.get("nagad_number")
            or wbom.get("employee_mobile")
        )
        phone = normalize_bd_phone(raw_phone) if raw_phone else None

        # Resolve or create the FPE employee
        emp = await match_or_create_employee(
            name_raw=wbom.get("employee_name"),
            payout_phone=phone,
            employee_id_phone=phone,
        )
        if not emp:
            log.warning(
                "[wbom_fpe_sync] could not resolve FPE employee for wbom_emp=%d",
                wbom["employee_id"],
            )
            return None

        method = _norm_method(wbom.get("payment_method"))
        amount = float(wbom.get("amount") or 0)
        txn_date_raw = wbom.get("transaction_date") or wbom.get("created_at")
        txn_date = (
            txn_date_raw.date()
            if txn_date_raw and hasattr(txn_date_raw, "date")
            else txn_date_raw
        )

        # Build a stable synthetic wa_message_id so accounting.create_transaction
        # can produce its own txn_ref dedup key.
        # Format: wbom:<transaction_id> — never collides with real WA message IDs.
        synthetic_wa_id = f"wbom:{wbom['transaction_id']}"

        fpe_txn = await create_transaction(
            employee_id=emp.employee_id,
            amount=amount,
            payout_method=method,
            wa_message_id=synthetic_wa_id,
            source=_SOURCE_WBOM,
            txn_date=txn_date,
            notes=(
                f"Synced from WBOM txn #{wbom['transaction_id']} "
                f"type={wbom.get('transaction_type')} "
                f"ref={wbom.get('reference_number') or ''}"
            ),
        )

        log.info(
            "[wbom_fpe_sync] wbom_txn=%d → fpe_txn=%s emp=%s amount=%.0f",
            wbom_txn_id,
            fpe_txn.get("id") if fpe_txn else "DEDUP",
            emp.employee_code,
            amount,
        )
        return fpe_txn

    except Exception as exc:
        log.error("[wbom_fpe_sync] sync_wbom_transaction(%d) error: %s", wbom_txn_id, exc)
        return None


async def backfill_wbom_to_fpe(since_days: int = 30) -> Tuple[int, int]:
    """
    Backfill all WBOM cash transactions from the last `since_days` days into
    the FPE pipeline.

    Returns (synced_count, skipped_count).  Never raises.
    """
    try:
        since = datetime.now(timezone.utc) - timedelta(days=since_days)
        rows = await fetch_all(
            """
            SELECT transaction_id FROM wbom_cash_transactions
            WHERE  created_at >= $1
            ORDER  BY transaction_id
            """,
            since,
        )
        synced = 0
        skipped = 0
        for row in rows:
            result = await sync_wbom_transaction(row["transaction_id"])
            if result:
                synced += 1
            else:
                skipped += 1

        log.info(
            "[wbom_fpe_sync] backfill complete since_days=%d synced=%d skipped=%d",
            since_days, synced, skipped,
        )
        return synced, skipped

    except Exception as exc:
        log.error("[wbom_fpe_sync] backfill_wbom_to_fpe error: %s", exc)
        return 0, 0
