# Git Snapshot — Fazle Core

**Checkpoint name:** `pre-chaos-stable-2026-05-07`
**Date:** 2026-05-07
**Status:** READY FOR CHAOS TESTING

---

## Current State

At this checkpoint:

- 165/165 smoke tests passing
- 59/59 workflow integration tests passing (full escort/payment lifecycle verified)
- 118/119 E2E Playwright tests passing (1 intentional skip)
- Payment correction module (reverse + adjust) fully implemented and tested
- README.md, TESTING_STATUS.md, BACKUP.md, RECOVERY.md created
- All known integration test failures from Batch 28 resolved

---

## Creating the Checkpoint Tag

```bash
cd /home/azim/core

# 1. Verify tests are green before tagging
source /home/azim/.venv/bin/activate
make smoke
python -m pytest tests/workflows/test_escort_payment_flow.py -m workflow --timeout=60 -q

# 2. Stage all documentation changes
git add README.md TESTING_STATUS.md BACKUP.md RECOVERY.md GIT_SNAPSHOT.md \
        PRE_CHAOS_CHECKLIST.md POST_CHAOS_VALIDATION.md CHAOS_TEST_PLAN.md

git add tests/workflows/test_escort_payment_flow.py
git add tests/conftest.py

# 3. Commit with structured message
git commit -m "checkpoint: pre-chaos-stable 2026-05-07

Validation results:
- 165/165 smoke tests passing
- 59/59 workflow integration tests passing
- 118/119 E2E Playwright tests passing (1 intentional skip)

Changes:
- tests/workflows/test_escort_payment_flow.py: all 59 passing
  - fix: test_escort_payment_draft_not_duplicated_on_retry
  - fix: test_duplicate_payment_finalize_not_allowed
  - fix: test_concurrent_release_events
  - fix: test_concurrent_payroll_compute
  - fix: test_adjust_payment_amount (expected_amount vs approved_amount)
- tests/conftest.py: add source_bridge, notes columns to fazle_payment_drafts
- docs: README.md rewritten (22 sections, ~500 lines)
- docs: TESTING_STATUS.md, BACKUP.md, RECOVERY.md, GIT_SNAPSHOT.md
- docs: PRE_CHAOS_CHECKLIST.md, POST_CHAOS_VALIDATION.md, CHAOS_TEST_PLAN.md

Known limitations documented:
- finalize_payment has no double-finalize guard (application-level guard exists)
- concurrent async operations bypass application-level dedup (acceptable at current load)
- handle_release_event returns ok=False on second call after Completed program

Refs: Batch-28 (payment-correction), Batch-25 (v1.0.1-hotfix)
"

# 4. Create annotated tag
git tag -a pre-chaos-stable-2026-05-07 \
  -m "Stable checkpoint before chaos/soak testing.
All 342 tests passing (343 total, 1 intentional skip).
Payment correction module verified.
Recovery and backup documentation complete."

# 5. Verify tag
git show pre-chaos-stable-2026-05-07 --stat
```

---

## Tag Naming Convention

```
<type>-<YYYY-MM-DD>[_HHMMSStz]

Types:
  pre-chaos-stable    Before chaos/soak test run
  post-chaos-stable   After chaos test verified clean
  hotfix              Emergency bug fix
  safepoint           General-purpose stable point
  v1.x.y              Release version
  v1.x.y-rcN          Release candidate

Examples:
  pre-chaos-stable-2026-05-07
  post-chaos-stable-2026-05-14
  safepoint-2026-05-06
  v1.0.1-hotfix
  v1.1.0-rc1
  v1.1.0
```

---

## Branch Strategy

```
main        <- Stable production code. Only merge after all tests pass.
             Tag after each stable checkpoint.

develop     <- Active development. Tests must pass before merging to main.

hotfix/*    <- Emergency fixes against main. Merge to both main and develop.

feature/*   <- New features. Branch from develop. Delete after merge.

chaos/*     <- Chaos test artifacts, scripts, reports. Never merge to main.
```

```bash
# Create feature branch
git checkout -b feature/batch29-rag-improvements develop

# Create hotfix
git checkout -b hotfix/double-finalize-guard main

# After hotfix: merge to main
git checkout main
git merge --no-ff hotfix/double-finalize-guard
git tag -a v1.0.2-hotfix -m "Fix double-finalize guard"

# After hotfix: merge to develop
git checkout develop
git merge --no-ff hotfix/double-finalize-guard
git branch -d hotfix/double-finalize-guard
```

---

## Rollback to This Checkpoint

```bash
# Stop app
sudo systemctl stop fazle-core.service

# Reset to this tag (hard — discards all uncommitted changes)
git fetch --tags
git reset --hard pre-chaos-stable-2026-05-07

# Reinstall if dependencies changed
source /home/azim/.venv/bin/activate
pip install -e . --quiet

# Restart
sudo systemctl start fazle-core.service
curl http://localhost:8200/health
```

---

## Previous Tags

| Tag | Date | Description |
|---|---|---|
| `v1.0.1-hotfix` | 2026-05 | Draft quality gate, admin command dedup |
| `v1.0.0` | 2026-04 | Initial production launch |
| `safepoint-2026-05-06` | 2026-05-06 | Ops patch: bulk-rejected 415 safe-mode drafts |
| `pre-chaos-stable-2026-05-07` | 2026-05-07 | **Current checkpoint** |

---

## Git State Commands

```bash
# View recent history
git log --oneline -15

# View all tags
git tag -l | sort -r

# What changed since last tag
git diff v1.0.1-hotfix --stat

# Show tag details
git show pre-chaos-stable-2026-05-07

# Check working tree status
git status
git stash list
```

---

## Notes

- `.env` is in `.gitignore` — always backup separately before tagging
- `logs/`, `.venv/`, `__pycache__/` are in `.gitignore`
- Bridge SQLite databases (message stores) are NOT in git
- Test DB is ephemeral (created fresh per test session via conftest)
