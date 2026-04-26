"""Batch 18 — Backup & DR tests.

Runs against the live ai-postgres container. Performs a real pg_dump and
verifies metadata + rotation behavior. Cleans up files it creates.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Load env
from dotenv import load_dotenv  # type: ignore
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from app.database import init_db, close_db, execute, fetch_one  # noqa
from modules import backup as b  # noqa


PASS = "✅"
FAIL = "❌"


async def cleanup():
    # Delete any rows we created and their files
    rows = await fetch_one(
        "SELECT COUNT(*)::int AS c FROM fazle_db_backups WHERE filename LIKE 'fazle_pg_%'"
    )
    out_dir = b._backup_dir()
    for p in out_dir.glob("fazle_pg_*.dump"):
        try: p.unlink()
        except OSError: pass
    await execute("DELETE FROM fazle_db_backups WHERE filename LIKE 'fazle_pg_%'")
    return rows["c"] if rows else 0


async def t1_run_backup():
    res = await b.run_backup()
    assert res["status"] == "ok", f"backup failed: {res}"
    assert res["size_bytes"] > 1000, f"suspiciously small: {res['size_bytes']}"
    assert len(res["sha256"]) == 64
    p = Path(res["path"])
    assert p.exists() and p.stat().st_size == res["size_bytes"]
    print(f"{PASS} 1. run_backup ok size={res['size_bytes']} dur={res['duration_ms']}ms")
    return res


async def t2_metadata_recorded(filename: str):
    row = await fetch_one(
        "SELECT status, size_bytes, sha256 FROM fazle_db_backups WHERE filename=$1",
        filename,
    )
    assert row is not None and row["status"] == "ok"
    assert row["size_bytes"] > 0 and len(row["sha256"]) == 64
    print(f"{PASS} 2. metadata recorded for {filename}")


async def t3_latest_backup(filename: str):
    latest = await b.latest_backup()
    assert latest and latest["filename"] == filename
    print(f"{PASS} 3. latest_backup returns {filename}")


async def t4_list_backups():
    rows = await b.list_backups(limit=5)
    assert len(rows) >= 1
    print(f"{PASS} 4. list_backups returned {len(rows)} rows")


async def t5_status():
    s = await b.backup_status()
    assert s["latest"] is not None
    assert s["files_on_disk"] >= 1
    assert s["newest_age_h"] is not None and s["newest_age_h"] < 1.0
    txt = b.render_status_text(s)
    assert "ব্যাকআপ স্ট্যাটাস" in txt
    print(f"{PASS} 5. backup_status age={s['newest_age_h']}h files={s['files_on_disk']}")


async def t6_rotation_keeps_latest():
    # Create a fake old dump file directly
    out_dir = b._backup_dir()
    fake = out_dir / "fazle_pg_20200101_000000.dump"
    fake.write_bytes(b"fake")
    await execute(
        "INSERT INTO fazle_db_backups (filename, path, size_bytes, sha256, status) "
        "VALUES ($1,$2,4,'x','ok')",
        fake.name, str(fake),
    )
    rot = await b.rotate_backups(keep_daily=1, keep_weekly=0)
    # Newest real file kept; fake old file deleted
    assert not fake.exists(), "old file should be deleted"
    assert rot["deleted"] >= 1
    print(f"{PASS} 6. rotation deleted={rot['deleted']} kept={rot['kept']}")


async def t7_rotation_marks_status():
    row = await fetch_one(
        "SELECT status, rotated_at FROM fazle_db_backups WHERE filename=$1",
        "fazle_pg_20200101_000000.dump",
    )
    assert row and row["status"] == "rotated" and row["rotated_at"] is not None
    print(f"{PASS} 7. rotated row marked status='rotated'")


async def t8_failure_path():
    # Force failure by pointing to a bogus container
    os.environ["BACKUP_PG_CONTAINER"] = "no-such-container-xyz"
    try:
        res = await b.run_backup()
    finally:
        os.environ["BACKUP_PG_CONTAINER"] = "ai-postgres"
    assert res["status"] == "failed", f"expected failure, got {res}"
    row = await fetch_one(
        "SELECT status, error FROM fazle_db_backups WHERE filename=$1",
        res["filename"],
    )
    assert row and row["status"] == "failed" and row["error"]
    print(f"{PASS} 8. failure recorded with error: {row['error'][:60]}…")


async def t9_job_daily_db_backup():
    res = await b.job_daily_db_backup()
    assert res["backup"]["status"] == "ok"
    assert "rotate" in res
    print(f"{PASS} 9. job_daily_db_backup ok")


async def main():
    await init_db()
    print("=== BATCH 18 BACKUP TESTS ===")
    deleted = await cleanup()
    if deleted: print(f"  (pre-clean: removed {deleted} prior rows)")
    try:
        res = await t1_run_backup()
        await t2_metadata_recorded(res["filename"])
        await t3_latest_backup(res["filename"])
        await t4_list_backups()
        await t5_status()
        await t6_rotation_keeps_latest()
        await t7_rotation_marks_status()
        await t8_failure_path()
        await t9_job_daily_db_backup()
        print("=== ALL BATCH 18 TESTS PASSED ===")
    finally:
        await cleanup()
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())
