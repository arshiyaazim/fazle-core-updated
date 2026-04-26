# Roadmap, Versioning & Batch Ledger

Fazle Core has been built incrementally as numbered batches. Each batch
added one focused capability with its own offline test. Detailed
implementation notes for every batch are in `/memories/repo/batch*.md`.

---

## Versioning model

Fazle Core ships as **immutable versions**. Each version is a frozen
copy of the app on the VPS:

```
/home/azim/fazle-core            ← live (v1.0)
/home/azim/fazle-core-v2-dev     ← in-progress copy (v2 work)
/home/azim/fazle-core-v1-archive ← previous version, kept for 30d rollback
```

Workflow for every new version:

1. **Copy** `cp -a /home/azim/fazle-core /home/azim/fazle-core-vN-dev`.
2. **Develop** new batches inside the copy on a different port (`8201`,
   `8202`, …) so v1 keeps serving traffic untouched.
3. **Test** against the copy until all batch tests + `scripts/run_ci.sh`
   pass and a smoke run on the copy is clean.
4. **Walk** [V1_LAUNCH_CHECKLIST.md](V1_LAUNCH_CHECKLIST.md) again for vN.
5. **Cut over**: stop the old systemd unit, start the new one, point
   nginx upstream to the new port. Keep the old folder for 30 days.
6. **Tag** `git tag -a vN.0 -m "Fazle Core vN.0"` and push.

Reasons:

- v1 stays frozen and predictable while you experiment.
- Rollback is `systemctl stop fazle-core-v2 && systemctl start fazle-core-v1`.
- Every version has its own `git tag`, its own DB-dump baseline, and its
  own copy of `.env` archived in `/home/azim/secure-env-backup/`.

Version naming:

| Bump | When |
|---|---|
| **Patch** (v1.0.x) | Hotfix only, in place on the live folder |
| **Minor** (v1.x.0) | New batches that don't break existing API/DB; in-place upgrade after CI |
| **Major** (vX.0.0) | Schema or API change, multi-tenant, big rewrite — always via the copy-then-cutover flow above |

---

## Done — v1.0 (B11 → B24)

| Batch | Focus | Highlights |
|---|---|---|
| **B11** | Recruitment gate | Funnel + scoring |
| **B12** | Payment ingest | Receipt parsing → drafts |
| **B13** | Escort lifecycle | State machine for escort jobs |
| **B14** | Payroll | Monthly compute + transition states |
| **B15** | Outbound resilience | Queue + retry + concurrency caps |
| **B16** | Scheduler | APScheduler, 8 jobs, Asia/Dhaka |
| **B17** | Reports | JSON/CSV export endpoints |
| **B18** | Backup | pg_dump + rotation |
| **B19** | RBAC & Audit | Roles + per-admin `fk_…` API keys + audit trail |
| **B20** | Admin Dashboard | Tabbed HTML at `/dashboard` |
| **B21** | RAG | BM25 over KB + extractive answers + citations |
| **B22** | Observability | In-proc metrics, Prometheus, HTTP middleware, error log surface |
| **B23** | CI | GitHub Actions + local `scripts/run_ci.sh` |
| **B24** | Docs | Full documentation set under `docs/` |

**v1.0 status:** linear plan complete. Ready for launch — see
[V1_LAUNCH_CHECKLIST.md](V1_LAUNCH_CHECKLIST.md).

## Test inventory

```
scripts/
  test_batch11_recruitment_gate.py
  test_batch12_payment_ingest.py
  test_batch13_escort_lifecycle.py
  test_batch14_payroll.py
  test_batch15_resilience.py
  test_batch16_scheduler.py
  test_batch17_reports.py
  test_batch18_backup.py
  test_batch19_rbac.py
  test_batch21_rag.py
  test_batch22_observability.py
  run_ci.sh                 # B23 — wraps the above + live tests
```

---

## Candidate batches for v1.x / v2

These are **ideas, not commitments**. They will be developed in a copy
of the app per the versioning model above.

| Batch | Theme | Outcome | Likely version |
|---|---|---|---|
| **B25** | Alerting | Push to Slack/PagerDuty when `errors_24h > N` or `health!=ok` for 2 min | v1.1 |
| **B26** | Multi-tenant | `tenant_id` column across drafts/audit/payroll; per-tenant API keys | v2.0 |
| **B27** | Hot-reload KB | File watcher on `resources/` triggers `/rag/reindex` automatically | v1.1 |
| **B28** | Bridge HA | Failover Bridge1 ↔ Bridge2 for the same WhatsApp number | v1.2 |
| **B29** | Voice replies | Piper TTS into outbound queue; auto-reply to voice notes | v1.2 |
| **B30** | Mobile admin PWA | Offline-capable PWA wrapper around `/dashboard` | v1.1 |
| **B31** | Analytics warehouse | Nightly export to ClickHouse / Parquet for BI | v2.x |
| **B32** | SaaS billing | Stripe + per-tenant usage caps from observability counters | v2.x |
| **B33** | Custom report builder | Owner builds reports from dashboard without writing SQL | v1.x |
| **B34** | Audit retention policy | Auto-archive `fazle_audit` rows > 365 days into cold storage | v1.x |

Pick whichever batch unblocks the most value, clone the app, build it
there, then come back and walk the launch checklist for the new version.

---

## Anti-roadmap (deliberately not doing)

To keep v1 honest, these are explicitly **out of scope**:

- No GraphQL — REST + dashboard is enough.
- No microservices split — single FastAPI process until proven needed.
- No external metrics backend until B25 — `/metrics` + the dashboard cover today's needs.
- No Kubernetes — systemd is the deploy target.
- No second LLM provider — Ollama local-only until cost data justifies otherwise.
