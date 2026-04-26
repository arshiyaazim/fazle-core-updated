# Version 1 Launch Checklist

**Status target:** Fazle Core v1.0 — production launch on VPS.
**Owner:** Azim · **Window:** mark each item ✅ before flipping
`AUTO_REPLY_ENABLED=true`.

> Convention: this is the **only** doc you tick before launch. Future
> versions (v1.1, v2.0) will branch off a copy of this app — see
> [ROADMAP.md](ROADMAP.md) → "Versioning model".

---

## 0. Pre-flight (5 min)

- [ ] `git -C /home/azim/fazle-core status` is clean (or all changes intentional).
- [ ] On `main` branch and pushed: `git log --oneline -3`.
- [ ] `bash scripts/run_ci.sh` → `✅ CI passed`.
- [ ] `sudo systemctl status fazle-core.service` → `active (running)`.
- [ ] `curl -s http://127.0.0.1:8200/health | jq` → `ok:true`.

---

## 1. Security

- [ ] `.env` permissions: `chmod 600 /home/azim/fazle-core/.env` (owner only).
- [ ] `INTERNAL_API_KEY` is ≥ 32 random chars (not the dev default).
- [ ] All admins have personal `fk_…` keys via `POST /admin/users/{phone}/apikey` — legacy key kept ONLY for break-glass.
- [ ] `ADMIN_NUMBERS` env contains the real superadmin phone(s); no test numbers.
- [ ] Service binds `127.0.0.1:8200` only (verify: `ss -tlnp | grep 8200`).
- [ ] Nginx in front terminates TLS (Let's Encrypt cert valid > 30 days):
      `curl -sI https://fazle.iamazim.com | head -1`.
- [ ] `ufw status` → only 22, 80, 443 open to the world.
- [ ] SSH: password auth disabled, only key auth (`/etc/ssh/sshd_config`).
- [ ] PostgreSQL not exposed publicly (`pg_isready -h <public-ip>` should fail).
- [ ] Webhook secrets rotated (Meta verify token, bridge HMAC) and saved to `secure-env-backup/`.
- [ ] No secrets in `git log -p | grep -iE "key|secret|password"` on `main`.
- [ ] `/metrics` reachable only on loopback: `curl --connect-to fazle.iamazim.com:443:127.0.0.1:80 https://fazle.iamazim.com/metrics` should 404 or be blocked at nginx.

## 2. Backup & Recovery

- [ ] `POST /backup/run` succeeds and writes a fresh dump to `/home/azim/backups/`.
- [ ] `POST /backup/rotate` keeps the policy size sane (e.g. 7 daily / 4 weekly).
- [ ] `/backup/status` shows last_run < 24h.
- [ ] Scheduler job `backup_daily` is listed in `GET /scheduler/status` with a `next_run` in the future.
- [ ] **Restore drill done at least once**: `pg_restore` a recent dump into a throwaway DB and confirm row counts.
- [ ] Off-VPS copy: at least one backup synced off the server (rsync to laptop, S3, or another VPS) — record method here:
      `__________________________`.
- [ ] `.env` backed up under `/home/azim/secure-env-backup/env_live_<date>.bak` and that folder is also off-VPS.

## 3. Daily Routine (15 min/day)

A copy-paste morning health pulse. Save as a phone shortcut.

```bash
KEY=$(grep ^INTERNAL_API_KEY= /home/azim/fazle-core/.env | cut -d= -f2-)

# 1. Liveness
curl -s http://127.0.0.1:8200/health | jq

# 2. Yesterday's load + errors
curl -s -H "X-Internal-Key: $KEY" http://127.0.0.1:8200/observability/summary | jq

# 3. Anything new in the error log?
curl -s -H "X-Internal-Key: $KEY" "http://127.0.0.1:8200/observability/errors?limit=20" | jq

# 4. Pending admin work
curl -s -H "X-Internal-Key: $KEY" http://127.0.0.1:8200/admin/drafts          | jq '.count'
curl -s -H "X-Internal-Key: $KEY" http://127.0.0.1:8200/admin/payment-drafts  | jq '.count'

# 5. Backup heartbeat
curl -s -H "X-Internal-Key: $KEY" http://127.0.0.1:8200/backup/status | jq
```

Daily checklist:

- [ ] Health is `ok`.
- [ ] `errors_24h` < your threshold (start with **20**; tune later).
- [ ] No draft older than 24h still pending (chase the approver).
- [ ] Backup ran in the last 24h.
- [ ] Disk: `df -h /` < 80% used.
- [ ] Bridge1 + Bridge2 still WhatsApp-linked (open the phone, check Linked Devices weekly).

## 4. Owner Dashboard

- [ ] `https://fazle.iamazim.com/dashboard` loads behind your X-Internal-Key.
- [ ] Tabs verified working: **Overview**, **Drafts**, **Payment Drafts**, **Recruitment**, **Audit**, **Reports**, **RAG (B21)**, **Observability (B22)**.
- [ ] Each tab returns data without console errors.
- [ ] Bookmark on phone home-screen labelled **Fazle Owner**.
- [ ] Owner knows the 6 critical numbers to glance at:
      uptime · http_total · errors_24h · drafts pending · payments pending · last backup.
- [ ] At least 1 trusted operator added via `POST /admin/users` with role `operator` so you are not single-point.

## 5. Monetization (v1 baseline)

The system is internal-first. v1 monetization = **cost recovery + value capture inside Al-Aqsa Security**. External SaaS comes in v2+.

- [ ] **Cost baseline written down**: VPS + domain + Meta WhatsApp + (optional) Ollama GPU = `__/month`.
- [ ] **Value tracked**: instrument savings — log
      `payments_processed_count`, `payroll_runs_completed`,
      `escort_slips_extracted`, `recruitment_candidates_screened` in
      `/admin/overview`. This becomes the ROI story.
- [ ] **Internal billing**: pick a chargeback model — flat monthly per
      department, OR per-message, OR per-payment-processed. Decision: `__________`.
- [ ] **Pricing sheet** drafted (even one page) for when a sister
      company asks "can we use this too?".
- [ ] **Usage cap** decided to protect the VPS until you upgrade:
      max msgs/day, max OCR/day, max payroll runs/month. Soft-limit via
      counters in `/observability/summary`; hard-limit later.
- [ ] **Audit-ready**: every payment draft has an audit row → you can
      prove who approved what (`GET /admin/audit`).
- [ ] **Branding**: dashboard title + WhatsApp reply signatures say
      "Al-Aqsa Security" not "Fazle Core" (customer-facing strings reviewed).

## 6. Go-Live Switch

When every box above is checked:

```bash
# 1. Flip SAFE MODE off
sed -i 's/^AUTO_REPLY_ENABLED=.*/AUTO_REPLY_ENABLED=true/' /home/azim/fazle-core/.env

# 2. Restart
sudo systemctl restart fazle-core.service && sleep 4

# 3. Confirm
curl -s -H "X-Internal-Key: $KEY" http://127.0.0.1:8200/admin/safe-mode | jq

# 4. Tag the release
cd /home/azim/fazle-core
git tag -a v1.0 -m "Fazle Core v1.0 — production launch"
git push origin v1.0
```

- [ ] `safe_mode: false` confirmed in API.
- [ ] Tag `v1.0` pushed.
- [ ] First real outbound message delivered (send a test from a known admin phone).
- [ ] Announcement to operators sent on WhatsApp: "v1 is live, use APPROVE/REJECT/EDIT/PAID/ADVANCE/STATUS as before."

## 7. After Launch — first 48 hours

- [ ] Watch `/observability/errors` every 2 hours.
- [ ] Watch `journalctl -u fazle-core -f` while you're awake.
- [ ] Keep `git checkout v1.0` rollback-ready: if anything breaks, restart from tag.
- [ ] No code changes on `main` for 48 h — only `hotfix/*` if absolutely necessary.

---

## What about v2, v3, …?

Per your plan: any new feature (B25 alerting, B26 multi-tenant, etc.)
is built in a **clone** of this app, not in place. Workflow:

1. `cp -a /home/azim/fazle-core /home/azim/fazle-core-v2-dev`
2. Develop + test in the copy on a different port (e.g. `8201`).
3. When stable, walk this same checklist again for v2.0 launch.
4. Cut over: stop v1 service, start v2 service, redirect nginx.
5. Keep v1 binaries + DB dump archived for 30 days as rollback path.

This keeps v1 frozen and reliable while v2 is built safely.
