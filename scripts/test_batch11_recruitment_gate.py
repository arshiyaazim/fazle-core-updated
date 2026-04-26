"""Batch 11 E2E: verify recruitment-only auto-reply gate + intake."""
import asyncio
import sys
sys.path.insert(0, "/home/azim/fazle-core")

from app.config import get_settings
from app.database import init_db, close_db, fetch_one, execute
from modules.recruitment_flow import (
    is_recruitment_trigger, get_active_session, intake_message,
)
from app.main import _should_recruitment_autoreply

TEST_PHONE = "8801999000111"  # synthetic, not an admin
ADMIN = "8801880446111"


async def main() -> int:
    settings = get_settings()
    print(f"recruitment_autoreply_enabled = {settings.recruitment_autoreply_enabled}")
    print(f"auto_reply_enabled            = {settings.auto_reply_enabled}")
    assert settings.recruitment_autoreply_enabled is True
    assert settings.auto_reply_enabled is False, "test assumes SAFE MODE on"

    await init_db()
    try:
        # Cleanup any prior test session
        await execute(
            "DELETE FROM fazle_recruitment_sessions WHERE phone = $1", TEST_PHONE
        )

        # 1. Trigger detection
        assert is_recruitment_trigger("চাকরি আছে?") is True
        assert is_recruitment_trigger("apply korbo") is True
        assert is_recruitment_trigger("ki obostha") is False
        print("OK trigger detection")

        # 2. Gate decisions
        assert await _should_recruitment_autoreply(TEST_PHONE, "চাকরি কি") is True
        assert await _should_recruitment_autoreply(TEST_PHONE, "ki obostha") is False
        assert await _should_recruitment_autoreply(ADMIN, "চাকরি কি") is False, "admin must NOT trip gate"
        print("OK gate decisions")

        # 3. Run intake — should create session and produce non-empty reply
        r1 = await intake_message(TEST_PHONE, "চাকরি লাগবে", source="bridge1")
        assert r1.get("reply"), f"intake should reply, got {r1}"
        print(f"OK intake start reply len={len(r1['reply'])}: {r1['reply'][:80]!r}")

        sess = await get_active_session(TEST_PHONE)
        assert sess is not None, "active session should exist after intake"
        print(f"OK session id={sess['id']} stage={sess.get('funnel_stage')} step={sess.get('collection_step')}")

        # 4. With active session, even a non-trigger message should pass the gate
        assert await _should_recruitment_autoreply(TEST_PHONE, "Mohammad Karim") is True
        print("OK active-session bypass works")

        # 5. Cleanup
        await execute("DELETE FROM fazle_recruitment_sessions WHERE phone = $1", TEST_PHONE)
        print("PASS Batch 11 recruitment auto-reply gate")
        return 0
    finally:
        await close_db()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
