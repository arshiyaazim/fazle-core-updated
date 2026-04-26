# Architecture

## High-level diagram

```
                    ┌──────────────────────────────────────┐
                    │         WhatsApp (Meta)              │
                    └────┬───────────────┬─────────────────┘
                         │               │
              webhook    │               │  bridge polling
                         ▼               ▼
                ┌────────────┐   ┌────────────┐   ┌────────────┐
                │  Meta WH   │   │ Bridge1    │   │ Bridge2    │
                │  (HTTPS)   │   │ HR :8080   │   │ OPS :8081  │
                └─────┬──────┘   └─────┬──────┘   └─────┬──────┘
                      │                │                │
                      └────────┬───────┴────────┬───────┘
                               ▼                ▼
                      ┌────────────────────────────────────┐
                      │    fazle-core (FastAPI :8200)      │
                      │  ┌──────────────────────────────┐  │
                      │  │ message_router (intent dispatch)│
                      │  │ ├─ admin_commands               │
                      │  │ ├─ escort / escort_lifecycle    │
                      │  │ ├─ recruitment_flow             │
                      │  │ ├─ payment_workflow             │
                      │  │ ├─ rag (KB search)              │
                      │  │ └─ outbound (queue + retry)     │
                      │  └──────────────────────────────┘  │
                      │  ┌──────────────────────────────┐  │
                      │  │ scheduler (apscheduler, 8 jobs)│ │
                      │  │ rbac · backup · reports         │
                      │  │ observability (metrics + logs)  │
                      │  └──────────────────────────────┘  │
                      └─────┬──────────────────┬───────────┘
                            │                  │
                            ▼                  ▼
                    ┌──────────────┐   ┌──────────────┐
                    │ PostgreSQL15 │   │   Ollama     │
                    │  (asyncpg)   │   │ qwen2.5:1.5b │
                    └──────────────┘   └──────────────┘
```

## Components

### fazle-core (this repo)
FastAPI app on `127.0.0.1:8200`, run by `systemd` unit
`fazle-core.service` as user `azim`. Single process, async I/O, in-memory
metrics. Bind is loopback-only — public traffic terminates at nginx.

### Bridges
Two Go binaries (`whatsapp-mcp`, `whatsapp2`) that maintain WhatsApp Web
sessions and write messages into local SQLite stores. fazle-core polls
both stores every 5s via `modules/bridge_poller`.

### PostgreSQL
Primary data store (`asyncpg`). Migrations live in `db/migrations/*.sql`,
applied in order on first boot. Schemas cover: employees, drafts,
payments, payroll runs, attendance, KB, recruitment sessions, RBAC,
audit, error log, escort lifecycle.

### Ollama
Local LLM for intent classification and reply drafting. Model:
`qwen2.5:1.5b`. Fazle-core uses `app/ollama.py` client with health
check; degrades gracefully if Ollama is down.

### Scheduler
APScheduler (`Asia/Dhaka`) runs 8 jobs: outbound queue drain, KB refresh,
backup rotate, RAG reindex, attendance import, error-log truncate, etc.
See `modules/scheduler.py`.

## Data flow — inbound message

1. Bridge poller picks up a new SQLite row.
2. `message_router.process_message(msg)` is called.
3. Intent classifier (`modules/intent`) labels it.
4. Routes to a workflow module (escort / recruitment / admin_commands).
5. Workflow may insert a **draft** in `fazle_drafts` (pending approval).
6. Admin sends `APPROVE 123` via WhatsApp → admin_commands marks the
   draft sent and forwards via the original bridge.
7. Every step writes to `fazle_audit` and increments observability counters.

## Module map

| Module | Role | Notes |
|---|---|---|
| `bridge_poller` | Tail two bridge SQLite stores | 5 s tick |
| `message_router` | Central inbound dispatch | logs `[ROLE]`, `[INTENT]` |
| `admin_commands` | APPROVE / REJECT / EDIT / PAID / ADVANCE / STATUS | |
| `intent` | LLM-based label | falls back to keyword heuristic |
| `escort` / `escort_lifecycle` / `escort_slip_extractor` | Slip OCR + pay drafts | OCR semaphore = 2 |
| `recruitment` / `recruitment_flow` | Candidate funnel | session table |
| `payment_workflow` / `payment_ingest` / `payment` | Pay drafts → finalize | |
| `payroll` / `payroll_logic` | Monthly run + transition state | |
| `attendance` / `attendance_parser` | Daily attendance import | |
| `knowledge_base` / `rag` | Static rules + BM25 search | B21 |
| `outbound` | Queued send with retry | B15 |
| `scheduler` | APScheduler glue | B16 |
| `reports` | CSV / JSON exports | B17 |
| `backup` | pg_dump + rotate | B18 |
| `rbac` | Roles, per-admin API keys | B19 |
| `observability` | counters/gauges/histograms + Prom | B22 |
| `media` / `image_hash` / `voice_processor` | Inbound media handling | |
| `identity_brain` / `employee_verification` / `user_role` | Phone → employee resolution | |

## Auth model (B19)

- Header: `X-Internal-Key`.
- Accepts the legacy `INTERNAL_API_KEY` (env) **or** any active per-admin
  key (`fk_<urlsafe32>`, sha256 lookup against `fazle_admins.api_key_hash`).
- `/metrics` is intentionally unauthenticated for localhost Prometheus
  scraping; safe because the service binds to `127.0.0.1`.
- Webhooks (`/webhook/meta`, `/webhook/mcp1`, `/webhook/mcp2`) verify
  bridge-specific shared secrets, not `X-Internal-Key`.

## Observability (B22)

- In-process metrics registry, no external deps.
- HTTP middleware records `fazle_http_requests_total` and
  `fazle_http_request_duration_ms` keyed by route **template** (not raw
  path) for bounded cardinality.
- Histogram buckets stored per-bucket, accumulated only at Prometheus
  render time.
- Errors flow into `fazle_error_log` via `app/error_log.py:record_error()`
  and surface at `/observability/errors`.

## CI (B23)

- `.github/workflows/ci.yml` — syntax + offline tests on every push/PR.
- `scripts/run_ci.sh` — same checks plus live B19 + B21 tests when
  fazle-core is reachable on `127.0.0.1:8200`.
