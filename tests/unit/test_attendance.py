"""Unit tests — modules/attendance"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch

pytestmark = pytest.mark.unit


class TestIsAttendanceMessage:
    """Test keyword detection for attendance messages."""

    @pytest.mark.parametrize("text", [
        "হাজির",
        "হাজির আছি",
        "উপস্থিত",
        "present",
        "on duty",
        "check in",
        "checked in",
        "আমি হাজির MV GOLDEN STAR",
        "Present sir",
    ])
    def test_attendance_keywords_detected(self, text):
        from modules.attendance import is_attendance_message
        assert is_attendance_message(text) is True

    @pytest.mark.parametrize("text", [
        "অগ্রিম লাগবে",
        "ডিউটি শেষ",
        "MV GOLDEN STAR escort lagbe",
        "চাকরি করতে চাই",
        "ধন্যবাদ",
        "hello",
    ])
    def test_non_attendance_not_detected(self, text):
        from modules.attendance import is_attendance_message
        assert is_attendance_message(text) is False


class TestHandleAttendanceMessage:
    """Test attendance draft creation flow."""

    async def test_creates_draft_for_known_employee(
        self, test_db_pool, seed_employee
    ):
        import app.database as db_module
        db_module._pool = test_db_pool

        from modules.attendance import handle_attendance_message

        result = await handle_attendance_message(
            sender_phone=seed_employee["employee_mobile"],
            text="হাজির আছি MV TEST",
            source="bridge1",
        )

        # A draft should be created
        async with test_db_pool.acquire() as conn:
            draft = await conn.fetchrow(
                "SELECT * FROM fazle_draft_replies WHERE intent='attendance' LIMIT 1"
            )
        assert draft is not None

    async def test_returns_none_for_unknown_sender(self, test_db_pool):
        """Unknown sender not in wbom_employees — should not crash."""
        import app.database as db_module
        db_module._pool = test_db_pool

        from modules.attendance import handle_attendance_message

        result = await handle_attendance_message(
            sender_phone="8801999999999",
            text="হাজির",
            source="bridge1",
        )
        # No crash — returns (reply_str, None) or a string
        assert result is None or isinstance(result, (str, tuple))

    async def test_phone_normalisation_0x(self, test_db_pool, seed_employee):
        """Employee stored as 8801811111111; lookup with 01811111111 (0X) should match via normalization."""
        import app.database as db_module
        db_module._pool = test_db_pool

        # seed_employee is already stored as 8801811111111 (880X format)
        # Call with 0X format — attendance module should normalize 0X → 880X
        from modules.attendance import handle_attendance_message

        await handle_attendance_message(
            sender_phone="01811111111",   # 0X format — should find 8801811111111
            text="হাজির আছি",
            source="bridge1",
        )

        async with test_db_pool.acquire() as conn:
            draft = await conn.fetchrow(
                "SELECT * FROM fazle_draft_replies WHERE intent='attendance' LIMIT 1"
            )
        assert draft is not None


class TestSaveAttendance:
    """Test attendance saving after admin APPROVE."""

    async def test_saves_present_row(self, test_db_pool, seed_employee):
        import app.database as db_module
        db_module._pool = test_db_pool

        from modules.attendance import save_attendance

        await save_attendance(
            employee_id=seed_employee["employee_id"],
            attendance_date=__import__('datetime').date.today(),
            status="Present",
            location="MV TEST VESSEL",
            recorded_by="8801700000001",
        )

        async with test_db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM wbom_attendance WHERE employee_id=$1 LIMIT 1",
                seed_employee["employee_id"],
            )
        assert row is not None
        assert row["status"] == "Present"

    async def test_duplicate_date_prevented(self, test_db_pool, seed_employee):
        """UNIQUE(employee_id, attendance_date) — second insert for same date should not raise."""
        import app.database as db_module
        db_module._pool = test_db_pool

        from modules.attendance import save_attendance

        # First insert
        today = __import__('datetime').date.today()
        await save_attendance(
            employee_id=seed_employee["employee_id"],
            attendance_date=today,
            status="Present",
            location="MV TEST",
            recorded_by="system",
        )
        # Second insert same day — should not raise
        await save_attendance(
            employee_id=seed_employee["employee_id"],
            attendance_date=today,
            status="Present",
            location="MV TEST",
            recorded_by="system",
        )

        async with test_db_pool.acquire() as conn:
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM wbom_attendance WHERE employee_id=$1",
                seed_employee["employee_id"],
            )
        assert count == 1, "Duplicate attendance row should not be inserted"
