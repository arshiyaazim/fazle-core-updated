"""
Fazle Payroll Engine — Reconciliation invariant.

Accounting principle (final architecture):
    ledger_sum + unmatched_review_sum  ==  all_detected_payment_amounts

Where:
    ledger_sum                 = SUM(amount) over fpe_cash_transactions
                                 WHERE NOT is_reversal  (verified ledger)
    unmatched_review_sum       = SUM(detected_amount) over fpe_unmatched_messages
                                 WHERE review_status = 'pending'
                                   AND detected_amount IS NOT NULL
                                 (money awaiting human review)
    all_detected_payment_amounts =
                  SUM((parsed_data->>'amount')::numeric) over fpe_parser_results
                  WHERE message_type = 'payment'
                    AND (parsed_data->>'amount') IS NOT NULL

This invariant tolerates pending review work — it does NOT require all parsed
amounts to be in the ledger. It only guarantees that no detected money has
silently disappeared between the parser and the operator's queue + ledger.

Excludes already-promoted unmatched rows (review_status='promoted') because
those amounts are now counted in the ledger, and counting them twice would
inflate the right-hand side.
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from app.database import fetch_val

log = logging.getLogger("fazle.fpe.reconcile")

# Tolerance for floating-point / rounding drift in the equality check.
TOLERANCE = Decimal("0.01")


async def compute_reconciliation(
    period: str | None = None,
    source: str | None = None,
) -> dict[str, Any]:
    """
    Compute the reconciliation snapshot.

    Filters:
      period  — accounting period 'YYYY-MM' (only for ledger; parser/unmatched
                are filtered by message month derived from fpe_wa_messages.timestamp_wa)
      source  — bridge1|bridge2|meta
    """
    where_period_msg = ""
    where_period_txn = ""
    where_source_msg = ""
    args: list[Any] = []

    if period:
        args.append(period)
        idx = len(args)
        where_period_msg = (
            f" AND to_char(m.timestamp_wa AT TIME ZONE 'UTC', 'YYYY-MM') = ${idx}"
        )
        where_period_txn = f" AND accounting_period = ${idx}"

    if source:
        args.append(source)
        idx = len(args)
        where_source_msg = f" AND m.source = ${idx}"

    parser_sum_q = f"""
        SELECT COALESCE(SUM((pr.parsed_data->>'amount')::numeric), 0)
        FROM fpe_parser_results pr
        JOIN fpe_wa_messages m ON m.id = pr.fpe_wa_message_id
        WHERE pr.message_type = 'payment'
          AND (pr.parsed_data->>'amount') IS NOT NULL
          AND m.is_from_me = TRUE
          {where_period_msg}
          {where_source_msg}
    """

    ledger_sum_q = f"""
        SELECT COALESCE(SUM(amount), 0)
        FROM fpe_cash_transactions
        WHERE NOT is_reversal
        {where_period_txn}
    """
    ledger_args = [period] if period else []

    unmatched_sum_q = f"""
        SELECT COALESCE(SUM(u.detected_amount), 0)
        FROM fpe_unmatched_messages u
        JOIN fpe_wa_messages m ON m.id = u.fpe_wa_message_id
        WHERE u.review_status = 'pending'
          AND u.detected_amount IS NOT NULL
          {where_period_msg}
          {where_source_msg}
    """

    parser_sum: Decimal = await fetch_val(parser_sum_q, *args) or Decimal("0")
    ledger_sum: Decimal = await fetch_val(ledger_sum_q, *ledger_args) or Decimal("0")
    unmatched_sum: Decimal = await fetch_val(unmatched_sum_q, *args) or Decimal("0")

    accounted = ledger_sum + unmatched_sum
    delta = parser_sum - accounted
    ok = abs(delta) <= TOLERANCE

    # Counts for operator dashboard
    pending_review = await fetch_val(
        "SELECT COUNT(*) FROM fpe_unmatched_messages WHERE review_status='pending'"
    ) or 0
    dlq_count = await fetch_val(
        """
        SELECT COUNT(*) FROM fpe_message_processing_state
        WHERE status='failed' AND attempts >= 5
        """
    ) or 0

    return {
        "filter": {"period": period, "source": source},
        "parser_detected_sum": str(parser_sum),
        "ledger_sum": str(ledger_sum),
        "unmatched_review_sum": str(unmatched_sum),
        "accounted_sum": str(accounted),
        "delta": str(delta),
        "tolerance": str(TOLERANCE),
        "ok": ok,
        "pending_review_count": int(pending_review),
        "dlq_count": int(dlq_count),
        "invariant": "ledger_sum + unmatched_review_sum == parser_detected_sum",
    }
