# API Reference

Base URL (loopback): `http://127.0.0.1:8200`

Auth header (where required): `X-Internal-Key: <KEY>` — accepts the
legacy env `INTERNAL_API_KEY` or any active per-admin `fk_…` key (B19).

Legend: 🔓 unauth · 🔐 internal key · 🪝 bridge webhook (own signature)

## Health & Meta

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/` | 🔓 | Welcome JSON |
| GET | `/health` | 🔓 | Liveness (DB ping + flags) |
| GET | `/health/deep` | 🔓 | Liveness + Ollama + bridges |
| GET | `/dashboard` | 🔓 | Admin dashboard (HTML) |
| GET | `/dashboard/legacy` | 🔓 | Pre-B20 dashboard |

## Webhooks

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/webhook/meta` | 🪝 | Meta verification challenge |
| POST | `/webhook/meta` | 🪝 | HMAC-signed Meta payload |
| POST | `/webhook/mcp1` | 🪝 | Bridge1 push (HR) |
| POST | `/webhook/mcp2` | 🪝 | Bridge2 push (OPS) |

## Send (outbound)

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/send/meta` | 🔐 | Send via Meta WhatsApp Cloud |
| POST | `/send/mcp1` | 🔐 | Send via Bridge1 |
| POST | `/send/mcp2` | 🔐 | Send via Bridge2 |

Body: `{ "to": "<phone>", "text": "<msg>" }`. SAFE_MODE
(`AUTO_REPLY_ENABLED=false`) suppresses actual delivery.

## Admin

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/admin/overview` | 🔐 | Counts: drafts, payments, jobs, errors |
| GET | `/admin/safe-mode` | 🔐 | Current safe-mode flag |
| GET | `/admin/drafts` | 🔐 | Pending reply drafts |
| GET | `/admin/payment-drafts` | 🔐 | Pending payment drafts |
| GET | `/admin/recruitment` | 🔐 | Active recruitment sessions |
| GET | `/admin/audit` | 🔐 | Audit trail (filterable) |

## Admin — RBAC (B19)

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/admin/users` | 🔐 | List admins + roles |
| POST | `/admin/users` | 🔐 | Create admin `{phone, role}` |
| POST | `/admin/users/{phone}/role` | 🔐 | Add role |
| DELETE | `/admin/users/{phone}/role/{role}` | 🔐 | Remove role |
| POST | `/admin/users/{phone}/disable` | 🔐 | Disable admin |
| POST | `/admin/users/{phone}/apikey` | 🔐 | Mint per-admin `fk_…` key |

Roles: `viewer (10) < operator (30) < accountant (50) < approver (70) < superadmin (100)`.

## Escort

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/escort-slip/extract` | 🔐 | OCR an uploaded slip image |
| POST | `/escort-slip/test-report` | 🔐 | Diagnostic OCR run |
| GET | `/escort-slip/extractions` | 🔐 | Recent OCR rows |
| POST | `/escort/release` | 🔐 | Mark escort released |

## Payment

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/payment/escort-draft` | 🔐 | Create escort pay draft |
| POST | `/payment/advance-draft` | 🔐 | Create advance draft |
| POST | `/payment/ingest` | 🔐 | Ingest external payment receipt |

## Payroll

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/payroll/compute` | 🔐 | Compute monthly run |
| GET | `/payroll/runs` | 🔐 | List runs |
| GET | `/payroll/runs/{id}` | 🔐 | Single run + lines |
| POST | `/payroll/run/{id}/transition` | 🔐 | Move state (draft → approved → paid) |

## Reports (B17)

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/reports` | 🔐 | List available report names |
| GET | `/reports/{name}` | 🔐 | Run report (JSON or `?format=csv`) |

## Backup (B18)

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/backup/run` | 🔐 | Trigger pg_dump now |
| POST | `/backup/rotate` | 🔐 | Apply retention policy |
| GET | `/backup/status` | 🔐 | Last run + sizes |
| GET | `/backup/list` | 🔐 | Files on disk |

## Scheduler (B16)

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/scheduler/status` | 🔐 | Jobs + next-run timestamps |
| POST | `/scheduler/run/{job}` | 🔐 | Trigger a job out-of-band |

## RAG (B21)

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/rag/reindex` | 🔐 | Rebuild BM25 index |
| GET | `/rag/search?q=…` | 🔐 | Top-K hits |
| GET | `/rag/answer?q=…` | 🔐 | Extractive answer + citations |
| GET | `/rag/stats` | 🔐 | Index health |

## Observability (B22)

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/metrics` | 🔓 | Prometheus text format (loopback only) |
| GET | `/metrics/json` | 🔐 | Full snapshot as JSON |
| GET | `/observability/summary` | 🔐 | Rollup: uptime, top paths, latency, errors_24h |
| GET | `/observability/errors?limit=50` | 🔐 | Recent rows from `fazle_error_log` |

## Examples

```bash
KEY=$(grep ^INTERNAL_API_KEY= /home/azim/fazle-core/.env | cut -d= -f2-)

# Health
curl -s http://127.0.0.1:8200/health | jq

# Overview
curl -s -H "X-Internal-Key: $KEY" http://127.0.0.1:8200/admin/overview | jq

# Mint a per-admin key
curl -s -X POST -H "X-Internal-Key: $KEY" \
     http://127.0.0.1:8200/admin/users/8801999000111/apikey | jq

# Prometheus scrape
curl -s http://127.0.0.1:8200/metrics | head
```
