"""Batch 19 — RBAC & Audit smoke test."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import init_db, close_db, execute, fetch_val
from modules import rbac


TEST_PHONE = "8801999000111"  # synthetic; cleaned up at start
TEST_PHONE2 = "8801999000222"


async def _cleanup():
    for p in (TEST_PHONE, TEST_PHONE2):
        aid = await fetch_val("SELECT id FROM fazle_admins WHERE phone=$1", p)
        if aid:
            await execute("DELETE FROM fazle_admin_audit WHERE actor_user_id=$1", aid)
            await execute("DELETE FROM fazle_admin_roles WHERE admin_id=$1", aid)
            await execute("DELETE FROM fazle_admins WHERE id=$1", aid)


async def main():
    await init_db()
    await _cleanup()
    print("=== Batch 19 — RBAC & Audit Test ===\n")

    # 1. bootstrap superadmins from env
    n = await rbac.ensure_bootstrap_admins()
    print(f"[1] bootstrap created/promoted: {n}")

    # 2. add a viewer admin
    res = await rbac.add_admin(TEST_PHONE, "test_viewer", role="viewer", granted_by="test")
    assert res["status"] == "created", f"add failed: {res}"
    print(f"[2] add_admin viewer → id={res['admin_id']}")

    # 3. permission check — allowed
    perm = await rbac.check_permission(phone=TEST_PHONE, command="status")
    assert perm["allowed"], f"status should be allowed: {perm}"
    print(f"[3] viewer 'status' → allowed (required={perm['required_role']})")

    # 4. permission check — denied
    perm2 = await rbac.check_permission(phone=TEST_PHONE, command="approve")
    assert not perm2["allowed"], f"approve should be denied: {perm2}"
    print(f"[4] viewer 'approve' → denied (need {perm2['required_role']}, reason={perm2['reason']})")

    # 5. record audit (allowed + denied)
    aid1 = await rbac.record_audit(
        channel="test", command="status",
        actor_phone=TEST_PHONE, actor_admin=perm["admin"],
        args="status", allowed=True, required_role="viewer",
        result_summary="ok",
    )
    aid2 = await rbac.record_audit(
        channel="test", command="approve",
        actor_phone=TEST_PHONE, actor_admin=perm2["admin"],
        args="APPROVE 1", allowed=False, required_role="operator",
        denied_reason=perm2["reason"],
    )
    print(f"[5] audit ids: allowed={aid1} denied={aid2}")

    # 6. promote to operator → approve now allowed
    await rbac.set_role(TEST_PHONE, "operator", granted_by="test")
    perm3 = await rbac.check_permission(phone=TEST_PHONE, command="approve")
    assert perm3["allowed"], f"approve should be allowed after promotion: {perm3}"
    print(f"[6] after set_role operator → 'approve' allowed")

    # 7. issue api key + lookup
    keyres = await rbac.issue_api_key(TEST_PHONE)
    assert keyres["api_key"].startswith("fk_"), f"key format: {keyres}"
    found = await rbac.get_admin_by_api_key(keyres["api_key"])
    assert found and found["phone"] == TEST_PHONE, f"key lookup failed: {found}"
    print(f"[7] issue_api_key → {keyres['api_key'][:14]}... lookup OK")

    # 8. disable
    await rbac.disable_admin(TEST_PHONE)
    perm4 = await rbac.check_permission(phone=TEST_PHONE, command="status")
    assert not perm4["allowed"], f"disabled should be denied: {perm4}"
    print(f"[8] disable_admin → check denied (reason={perm4['reason']})")

    # 9. add second admin + list
    await rbac.add_admin(TEST_PHONE2, "test_acct", role="accountant", granted_by="test")
    rows = await rbac.list_admins()
    assert len(rows) >= 2, f"list short: {len(rows)}"
    print(f"[9] list_admins → {len(rows)} admins total")
    for r in rows:
        if r["phone"] in (TEST_PHONE, TEST_PHONE2):
            print(f"     • {r['phone']} [{r['status']}] roles={r['roles']}")

    # 10. list_audit
    audit = await rbac.list_audit(limit=10)
    assert len(audit) >= 2, f"audit short: {len(audit)}"
    print(f"[10] list_audit(10) → {len(audit)} rows")

    # cleanup
    await _cleanup()
    print("\n✅ Batch 19 RBAC & Audit — ALL TESTS PASS")
    await close_db()


if __name__ == "__main__":
    asyncio.run(main())
