"""
Batch 26 — Reviewed reply memory tests (offline).
Run: /home/azim/.venv/bin/python scripts/test_batch26_reviewed_reply_memory.py
"""
import asyncio
import os
import sys
import types
from unittest.mock import AsyncMock, patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

fake_scheduler = types.ModuleType("modules.scheduler")
fake_scheduler.start_scheduler = lambda: None
fake_scheduler.stop_scheduler = lambda: None
sys.modules.setdefault("modules.scheduler", fake_scheduler)

fake_outbound = types.ModuleType("modules.outbound")
fake_outbound.start_background_worker = lambda: None
fake_outbound.stop_background_worker = AsyncMock()
sys.modules.setdefault("modules.outbound", fake_outbound)

from app import main as app_main
from modules import message_router as router
from modules import reviewed_reply_memory as rrm


async def test_reviewed_lookup_prefers_phone_scoped_exact_match():
    attempts = []

    async def fake_lookup_scope(*, intent, role, draft_type, last10_phone):
        attempts.append((intent, role, draft_type, last10_phone))
        if last10_phone == "1234567890":
            return {"id": 11, "match_scope": "intent_role_phone", "status": "active"}
        return None

    with patch.object(rrm, "_feature_enabled", return_value=True), \
         patch.object(rrm, "_lookup_scope", new=fake_lookup_scope), \
         patch.object(rrm, "execute", new=AsyncMock()), \
         patch.object(rrm, "fetch_one", new=AsyncMock(return_value={"id": 11, "match_scope": "intent_role_phone", "status": "active"})):
        row = await rrm.lookup_reviewed_reply(
            sender_phone="880171234567890",
            intent="salary_query",
            role="employee",
        )

    assert row and row["id"] == 11
    assert attempts[0] == ("salary_query", "employee", "generic", "1234567890")


async def test_reviewed_lookup_falls_back_to_intent_role_after_phone_miss():
    attempts = []

    async def fake_lookup_scope(*, intent, role, draft_type, last10_phone):
        attempts.append((intent, role, draft_type, last10_phone))
        if last10_phone == "":
            return {"id": 22, "match_scope": "intent_role", "status": "active"}
        return None

    with patch.object(rrm, "_feature_enabled", return_value=True), \
         patch.object(rrm, "_lookup_scope", new=fake_lookup_scope), \
         patch.object(rrm, "execute", new=AsyncMock()), \
         patch.object(rrm, "fetch_one", new=AsyncMock(return_value={"id": 22, "match_scope": "intent_role", "status": "active"})):
        row = await rrm.lookup_reviewed_reply(
            sender_phone="880171234567890",
            intent="salary_query",
            role="employee",
        )

    assert row and row["id"] == 22
    assert attempts[:2] == [
        ("salary_query", "employee", "generic", "1234567890"),
        ("salary_query", "employee", "generic", ""),
    ]


async def test_reviewed_lookup_blocks_wrong_role():
    with patch.object(rrm, "_feature_enabled", return_value=True), \
         patch.object(rrm, "_lookup_scope", new=AsyncMock()) as lookup_mock:
        row = await rrm.lookup_reviewed_reply(
            sender_phone="8801712345678",
            intent="salary_query",
            role="",
        )

    assert row is None
    assert lookup_mock.await_count == 0


async def test_create_or_update_from_edit_creates_new_entry():
    draft = {
        "id": 5,
        "source": "bridge2",
        "recipient": "8801712345678",
        "intent": "salary_query",
        "draft_type": "generic",
        "reply_text": "old",
        "status": "pending",
        "meta": {"role": "employee"},
    }
    with patch.object(rrm, "_feature_enabled", return_value=True), \
         patch.object(rrm, "check_draft_quality", return_value=(True, None)), \
         patch.object(rrm, "fetch_one", new=AsyncMock(side_effect=[None, {"id": 90, "status": "active"}])), \
         patch.object(rrm, "fetch_val", new=AsyncMock(return_value=90)), \
         patch.object(rrm, "execute", new=AsyncMock()):
        row = await rrm.create_or_update_from_edit(draft_row=draft, new_text="new reply", admin_phone="8801880446111")

    assert row and row["id"] == 90


async def test_create_or_update_from_edit_updates_existing_same_scope_entry():
    draft = {
        "id": 5,
        "source": "bridge2",
        "recipient": "8801712345678",
        "intent": "salary_query",
        "draft_type": "generic",
        "reply_text": "old",
        "status": "pending",
        "meta": {"role": "employee"},
    }
    existing = {"id": 91}
    with patch.object(rrm, "_feature_enabled", return_value=True), \
         patch.object(rrm, "check_draft_quality", return_value=(True, None)), \
         patch.object(rrm, "fetch_one", new=AsyncMock(side_effect=[existing, {"id": 91, "status": "active"}])), \
         patch.object(rrm, "execute", new=AsyncMock()) as exec_mock:
        row = await rrm.create_or_update_from_edit(draft_row=draft, new_text="updated reply", admin_phone="8801880446111")

    assert row and row["id"] == 91
    assert exec_mock.await_count == 1


async def test_message_router_uses_reviewed_reply_before_generic_ai_fallback():
    identity = {"role": "employee", "identity_confidence": 1.0, "identity_source": "test", "employee_id": None}
    with patch.object(router, "detect_identity", new=AsyncMock(return_value=identity)), \
         patch.object(router, "classify", return_value="salary_query"), \
         patch.object(router, "kb_get_reply", new=AsyncMock(return_value=None)), \
         patch.object(router, "lookup_reviewed_reply", new=AsyncMock(return_value={"reply_text": "reviewed salary reply"})), \
         patch.object(router.ai, "generate_reply", new=AsyncMock(return_value="ai reply")) as ai_mock, \
         patch.object(router, "get_contact_context", new=AsyncMock(return_value="ctx")), \
         patch.object(router, "get_verification_session", new=AsyncMock(return_value=None)), \
         patch.object(router, "check_identity_mismatch", new=AsyncMock(return_value=None)):
        reply, admin_note = await router.process_message("8801712345678", "salary?", "bridge2")

    assert reply == "reviewed salary reply"
    assert admin_note is None
    assert ai_mock.await_count == 0


async def test_message_router_preserves_current_behavior_on_reviewed_miss():
    identity = {"role": "employee", "identity_confidence": 1.0, "identity_source": "test", "employee_id": None}
    with patch.object(router, "detect_identity", new=AsyncMock(return_value=identity)), \
         patch.object(router, "classify", return_value="salary_query"), \
         patch.object(router, "kb_get_reply", new=AsyncMock(return_value=None)), \
         patch.object(router, "lookup_reviewed_reply", new=AsyncMock(return_value=None)), \
         patch.object(router.ai, "generate_reply", new=AsyncMock(return_value="ai reply")) as ai_mock, \
         patch.object(router, "get_contact_context", new=AsyncMock(return_value="ctx")), \
         patch.object(router, "get_verification_session", new=AsyncMock(return_value=None)), \
         patch.object(router, "check_identity_mismatch", new=AsyncMock(return_value=None)):
        reply, admin_note = await router.process_message("8801712345678", "salary?", "bridge2")

    assert reply == "ai reply"
    assert admin_note is None
    assert ai_mock.await_count == 1


async def test_admin_reviewed_replies_list_endpoint_filters_active_entries():
    rows = [{"id": 1, "status": "active"}]
    with patch.object(app_main, "list_reviewed_replies", new=AsyncMock(return_value=rows)):
        payload = await app_main.list_reviewed_replies_api(limit=10, status="active")

    assert payload["count"] == 1
    assert payload["reviewed_replies"][0]["id"] == 1


async def test_admin_reviewed_reply_disable_endpoint_blocks_future_reuse():
    with patch.object(app_main, "disable_reviewed_reply", new=AsyncMock(return_value={"id": 8, "status": "disabled"})):
        payload = await app_main.disable_reviewed_reply_api(8, payload={"reason": "too broad"})

    assert payload == {"ok": True, "id": 8, "status": "disabled"}


async def test_reviewed_lookup_bypassed_when_feature_flag_disabled():
    with patch.object(rrm, "_feature_enabled", return_value=False), \
         patch.object(rrm, "_lookup_scope", new=AsyncMock()) as lookup_mock:
        row = await rrm.lookup_reviewed_reply(
            sender_phone="8801712345678",
            intent="salary_query",
            role="employee",
        )

    assert row is None
    assert lookup_mock.await_count == 0


async def main():
    print("[1] phone-scoped reviewed lookup wins")
    await test_reviewed_lookup_prefers_phone_scoped_exact_match()
    print("    ok")

    print("[2] reviewed lookup falls back to broader scope")
    await test_reviewed_lookup_falls_back_to_intent_role_after_phone_miss()
    print("    ok")

    print("[3] reviewed lookup blocks empty role")
    await test_reviewed_lookup_blocks_wrong_role()
    print("    ok")

    print("[4] edit creates a reviewed entry")
    await test_create_or_update_from_edit_creates_new_entry()
    print("    ok")

    print("[5] edit updates existing reviewed entry")
    await test_create_or_update_from_edit_updates_existing_same_scope_entry()
    print("    ok")

    print("[6] router uses reviewed reply before generic AI fallback")
    await test_message_router_uses_reviewed_reply_before_generic_ai_fallback()
    print("    ok")

    print("[7] router falls back to AI when reviewed lookup misses")
    await test_message_router_preserves_current_behavior_on_reviewed_miss()
    print("    ok")

    print("[8] list endpoint returns reviewed entries")
    await test_admin_reviewed_replies_list_endpoint_filters_active_entries()
    print("    ok")

    print("[9] disable endpoint returns disabled status")
    await test_admin_reviewed_reply_disable_endpoint_blocks_future_reuse()
    print("    ok")

    print("[10] feature flag bypass works")
    await test_reviewed_lookup_bypassed_when_feature_flag_disabled()
    print("    ok")

    print("\n✅ Batch 26 Reviewed Reply Memory — CORE TESTS PASS")


if __name__ == "__main__":
    asyncio.run(main())