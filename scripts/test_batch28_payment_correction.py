"""
Offline tests for B28 — Payment Correction & Reversal.

Run with: /home/azim/.venv/bin/python scripts/test_batch28_payment_correction.py
"""
import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

# ── Ensure project on path ───────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent.parent))

# ── Pre-mock app.database and modules.observability so the module can load ───
import unittest.mock as _mock

_db_mock = _mock.MagicMock()
_db_mock.execute   = AsyncMock(return_value=None)
_db_mock.fetch_one = AsyncMock(return_value=None)
_db_mock.fetch_val = AsyncMock(return_value=None)
_db_mock.fetch_all = AsyncMock(return_value=[])

_obs_mock = _mock.MagicMock()
_obs_mock.inc = _mock.MagicMock()

sys.modules.setdefault("app", _mock.MagicMock())
sys.modules.setdefault("app.database", _db_mock)
sys.modules.setdefault("app.config", _mock.MagicMock())
sys.modules.setdefault("modules.observability", _obs_mock)

# Force reload so our mocks are used
import importlib
if "modules.payment_correction" in sys.modules:
    del sys.modules["modules.payment_correction"]
import modules.payment_correction as _pc

PASS = 0
FAIL = 0

def ok(msg):
    global PASS
    PASS += 1
    print(f"  ✅ PASS  {msg}")

def fail(msg, exc=None):
    global FAIL
    FAIL += 1
    detail = f" — {exc}" if exc else ""
    print(f"  ❌ FAIL  {msg}{detail}")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_draft(status="approved", amount=5000.0, correction_type=None, correction_of=None):
    return {
        "id": 10, "employee_id": 55, "employee_name": "Rahim",
        "employee_mobile": "01700000001", "escort_program_id": 3,
        "draft_type": "escort_payment", "expected_amount": amount,
        "approved_amount": amount, "payment_method": "bkash",
        "payment_number": "01700000001", "source_bridge": "bridge1",
        "status": status, "correction_type": correction_type,
        "correction_of": correction_of,
    }

def _make_tx(tx_id=201, amount=5000.0):
    return {
        "transaction_id": tx_id, "amount": amount,
        "payment_method": "bkash", "transaction_type": "payment",
    }


# ── Test 1 — reverse_payment happy-path ───────────────────────────────────────

async def _test_reverse_happy():
    draft = _make_draft(status="approved")
    tx    = _make_tx()

    _pc.fetch_one = AsyncMock(side_effect=[draft, tx])
    _pc.fetch_val = AsyncMock(return_value=202)
    _pc.execute   = AsyncMock(return_value=None)
    _pc.obs       = _obs_mock

    res = await _pc.reverse_payment(draft_id=10, admin_phone="01900000001", reason="client cancel")

    assert res["ok"], f"expected ok=True, got {res}"
    assert res["draft_id"] == 10
    assert res["original_transaction_id"] == 201
    assert res["counter_transaction_id"] == 202
    assert res["reversed_amount"] == 5000.0
    ok("reverse_payment happy-path")


# ── Test 2 — reverse_payment rejects wrong status ─────────────────────────────

async def _test_reverse_wrong_status():
    draft = _make_draft(status="pending")
    _pc.fetch_one = AsyncMock(return_value=draft)

    res = await _pc.reverse_payment(draft_id=10, admin_phone="01900000001", reason="oops")

    assert not res["ok"]
    assert "pending" in res["error"]
    ok("reverse_payment rejects 'pending' draft")


# ── Test 3 — adjust_payment happy-path ────────────────────────────────────────

async def _test_adjust_happy():
    draft = _make_draft(status="approved", amount=5000.0)

    _pc.fetch_one = AsyncMock(side_effect=[draft, None])
    _pc.fetch_val = AsyncMock(return_value=11)
    _pc.execute   = AsyncMock(return_value=None)
    _pc.obs       = _obs_mock

    res = await _pc.adjust_payment(
        draft_id=10, new_amount=5500.0, method="cash",
        admin_phone="01900000001", reason="rate revised",
    )

    assert res["ok"], f"expected ok=True, got {res}"
    assert res["adjustment_draft_id"] == 11
    assert res["original_draft_id"] == 10
    assert round(res["diff"], 2) == 500.0
    ok("adjust_payment happy-path")


# ── Test 4 — adjust_payment blocks duplicate pending adjustment ───────────────

async def _test_adjust_duplicate_blocked():
    draft    = _make_draft(status="approved", amount=5000.0)
    existing = {"id": 99}

    _pc.fetch_one = AsyncMock(side_effect=[draft, existing])

    res = await _pc.adjust_payment(
        draft_id=10, new_amount=6000.0, method="bkash",
        admin_phone="01900000001", reason="again",
    )

    assert not res["ok"]
    assert "99" in res["error"]
    ok("adjust_payment blocks duplicate pending adjustment")


# ── Test 5 — adjust_payment rejects zero/negative amount ─────────────────────

async def _test_adjust_bad_amount():
    res = await _pc.adjust_payment(
        draft_id=10, new_amount=0.0, method="cash",
        admin_phone="01900000001", reason="test",
    )
    assert not res["ok"]
    ok("adjust_payment rejects zero amount")


# ── Runner ─────────────────────────────────────────────────────────────────────

async def _main():
    print("\n═══  B28 Payment Correction — Offline Tests  ═══\n")
    for fn in [
        _test_reverse_happy,
        _test_reverse_wrong_status,
        _test_adjust_happy,
        _test_adjust_duplicate_blocked,
        _test_adjust_bad_amount,
    ]:
        try:
            await fn()
        except Exception as e:
            fail(fn.__name__, e)

    print(f"\n{'─'*42}")
    print(f"  Total: {PASS+FAIL}  ✅ {PASS}  ❌ {FAIL}")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    asyncio.run(_main())


PASS = 0
FAIL = 0

def ok(msg):
    global PASS
    PASS += 1
    print(f"  ✅ PASS  {msg}")

def fail(msg, exc=None):
    global FAIL
    FAIL += 1
    detail = f" — {exc}" if exc else ""
    print(f"  ❌ FAIL  {msg}{detail}")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_draft(status="approved", amount=5000.0, correction_type=None, correction_of=None):
    return {
        "id": 10, "employee_id": 55, "employee_name": "Rahim",
        "employee_mobile": "01700000001", "escort_program_id": 3,
        "draft_type": "escort_payment", "expected_amount": amount,
        "approved_amount": amount, "payment_method": "bkash",
        "payment_number": "01700000001", "source_bridge": "bridge1",
        "status": status, "correction_type": correction_type,
        "correction_of": correction_of,
    }

def _make_tx(tx_id=201, amount=5000.0):
    return {
        "transaction_id": tx_id, "amount": amount,
        "payment_method": "bkash", "transaction_type": "payment",
    }


# ── Test 1 — reverse_payment happy-path ───────────────────────────────────────

async def _test_reverse_happy():
    draft = _make_draft(status="approved")
    tx    = _make_tx()

    with (
        patch("modules.payment_correction.fetch_one", new=AsyncMock(side_effect=[draft, tx])),
        patch("modules.payment_correction.fetch_val", new=AsyncMock(return_value=202)),
        patch("modules.payment_correction.execute",   new=AsyncMock(return_value=None)),
        patch("modules.payment_correction.obs",        MagicMock()),
    ):
        import modules.payment_correction as _m
        res = await _m.reverse_payment(draft_id=10, admin_phone="01900000001", reason="client cancel")

    assert res["ok"], f"expected ok=True, got {res}"
    assert res["draft_id"] == 10
    assert res["original_transaction_id"] == 201
    assert res["counter_transaction_id"] == 202
    assert res["reversed_amount"] == 5000.0
    ok("reverse_payment happy-path")

# ── Test 2 — reverse_payment rejects wrong status ─────────────────────────────

async def _test_reverse_wrong_status():
    draft = _make_draft(status="pending")

    with patch("modules.payment_correction.fetch_one", new=AsyncMock(return_value=draft)):
        from modules.payment_correction import reverse_payment
        res = await reverse_payment(draft_id=10, admin_phone="01900000001", reason="oops")

    assert not res["ok"]
    assert "pending" in res["error"]
    ok("reverse_payment rejects 'pending' draft")

# ── Test 3 — adjust_payment happy-path ────────────────────────────────────────

async def _test_adjust_happy():
    draft = _make_draft(status="approved", amount=5000.0)

    with (
        patch("modules.payment_correction.fetch_one", new=AsyncMock(side_effect=[draft, None])),
        patch("modules.payment_correction.fetch_val", new=AsyncMock(return_value=11)),
        patch("modules.payment_correction.execute",   new=AsyncMock(return_value=None)),
        patch("modules.payment_correction.obs",        MagicMock()),
    ):
        from modules.payment_correction import adjust_payment
        res = await adjust_payment(
            draft_id=10, new_amount=5500.0, method="cash",
            admin_phone="01900000001", reason="rate revised",
        )

    assert res["ok"], f"expected ok=True, got {res}"
    assert res["adjustment_draft_id"] == 11
    assert res["original_draft_id"] == 10
    assert res["diff"] == pytest_round(5500.0 - 5000.0)
    ok("adjust_payment happy-path")

def pytest_round(v):
    return round(v, 2)

# ── Test 4 — adjust_payment blocks duplicate pending adjustment ───────────────

async def _test_adjust_duplicate_blocked():
    draft      = _make_draft(status="approved", amount=5000.0)
    existing   = {"id": 99}  # existing pending adjustment

    with (
        patch("modules.payment_correction.fetch_one", new=AsyncMock(side_effect=[draft, existing])),
    ):
        from modules.payment_correction import adjust_payment
        res = await adjust_payment(
            draft_id=10, new_amount=6000.0, method="bkash",
            admin_phone="01900000001", reason="again",
        )

    assert not res["ok"]
    assert "99" in res["error"]
    ok("adjust_payment blocks duplicate pending adjustment")

# ── Test 5 — adjust_payment rejects zero/negative amount ─────────────────────

async def _test_adjust_bad_amount():
    from modules.payment_correction import adjust_payment
    res = await adjust_payment(
        draft_id=10, new_amount=0.0, method="cash",
        admin_phone="01900000001", reason="test",
    )
    assert not res["ok"]
    assert "0" in res["error"]
    ok("adjust_payment rejects zero amount")

# ── Runner ─────────────────────────────────────────────────────────────────────

async def _main():
    print("\n═══  B28 Payment Correction — Offline Tests  ═══\n")
    for fn in [
        _test_reverse_happy,
        _test_reverse_wrong_status,
        _test_adjust_happy,
        _test_adjust_duplicate_blocked,
        _test_adjust_bad_amount,
    ]:
        try:
            await fn()
        except Exception as e:
            fail(fn.__name__, e)

    print(f"\n{'─'*42}")
    print(f"  Total: {PASS+FAIL}  ✅ {PASS}  ❌ {FAIL}")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    asyncio.run(_main())
