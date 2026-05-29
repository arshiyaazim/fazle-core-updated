"""Read-only employee and payment lookups from existing wbom_* tables.

SELECT only. No writes. No schema changes. No new tables.
"""
from __future__ import annotations

import re
from typing import Any

from app.database import fetch_all, fetch_one, fetch_val


def _normalize_mobile(raw: str) -> str:
    digits = re.sub(r"\D", "", raw)
    return digits[-10:] if len(digits) >= 10 else digits


async def find_by_mobile(mobile: str) -> dict[str, Any] | None:
    last10 = _normalize_mobile(mobile)
    if len(last10) < 9:
        return None
    return await fetch_one(
        """
        SELECT employee_id, employee_name, designation, basic_salary, status,
               employee_mobile, joining_date
        FROM wbom_employees
        WHERE RIGHT(REGEXP_REPLACE(COALESCE(employee_mobile, ''), '[^0-9]', '', 'g'), 10) = $1
          AND status NOT IN ('deleted', 'terminated')
        LIMIT 1
        """,
        last10,
    )


async def find_by_name(name: str) -> dict[str, Any] | None:
    return await fetch_one(
        """
        SELECT employee_id, employee_name, designation, basic_salary, status,
               employee_mobile, joining_date
        FROM wbom_employees
        WHERE lower(employee_name) LIKE lower($1)
          AND status NOT IN ('deleted', 'terminated')
        ORDER BY employee_id DESC
        LIMIT 1
        """,
        f"%{name.strip()}%",
    )


async def get_payment_history(employee_id: int) -> list[dict[str, Any]]:
    return await fetch_all(
        """
        SELECT amount, payment_method, transaction_date, status
        FROM wbom_cash_transactions
        WHERE employee_id = $1
          AND status IN ('completed', 'paid', 'approved')
        ORDER BY transaction_date DESC
        LIMIT 15
        """,
        employee_id,
    )


async def get_total_paid(employee_id: int) -> float:
    val = await fetch_val(
        """
        SELECT COALESCE(SUM(amount), 0)
        FROM wbom_cash_transactions
        WHERE employee_id = $1
          AND status IN ('completed', 'paid', 'approved')
        """,
        employee_id,
    )
    return float(val or 0)


async def get_program_count(employee_id: int) -> int:
    val = await fetch_val(
        """
        SELECT COUNT(*)
        FROM wbom_escort_programs
        WHERE employee_id = $1
          AND status IN ('completed', 'released', 'closed')
        """,
        employee_id,
    )
    return int(val or 0)
