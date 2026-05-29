# Fazle Core — WhatsApp AI Backend

**Al-Aqsa Security & Logistics Services Ltd.**
Production system — last updated 2026-05-29

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Architecture](#2-architecture)
3. [Services & Ports](#3-services--ports)
4. [WhatsApp Bridges](#4-whatsapp-bridges)
5. [Message Routing Pipeline](#5-message-routing-pipeline)
6. [Module Index](#6-module-index)
7. [Database Tables](#7-database-tables)
8. [Key Configuration](#8-key-configuration)
9. [Auto-Reply & Draft Policy](#9-auto-reply--draft-policy)
10. [Contact Exclusion & Silent Skip](#10-contact-exclusion--silent-skip)
11. [Outbound Queue & Circuit Breaker](#11-outbound-queue--circuit-breaker)
12. [Media Processing (STT / OCR)](#12-media-processing-stt--ocr)
13. [Fazle Payroll Engine (FPE)](#13-fazle-payroll-engine-fpe)
14. [Admin Commands](#14-admin-commands)
15. [Operations Runbook](#15-operations-runbook)
16. [Changelog & Update Policy](#16-changelog--update-policy)
17. [AI Knowledge Hierarchy](#17-ai-knowledge-hierarchy)
18. [Production Auto-Reply Policy](#18-production-auto-reply-policy)

---

## 1. Project Overview

Fazle Core is a FastAPI-based AI backend that connects two physical WhatsApp phone numbers and one Meta WhatsApp Cloud API number to a unified message intelligence pipeline. It:

- Polls two local WhatsApp bridge SQLite stores every 5 seconds for new inbound DMs
- Receives Meta WhatsApp Cloud API webhooks for a third number
- Classifies intent, resolves contact identity, and generates replies via Ollama (LLM) and a knowledge base (RAG)
- Applies a configurable draft-vs-autosend policy before delivering any reply
- Drives the Fazle Payroll Engine (FPE) for cash/income recording via WhatsApp commands
- Manages an escort-order lifecycle from order creation through payment

**Current mode:** `AUTO_REPLY_ENABLED=true` — activated 2026-05-29 (Phase 4.5)

Auto-send is active for these paths (see §18 for full policy):
- **Recruitment information** — job queries, vacancy, joining process, fee, greeting
- **Employee information** — salary schedule, duty info, attendance/leave policy, food/transport policy
- **Admin APPROVE command** — explicit manual approval bypasses all gates

The following always produce drafts regardless of `AUTO_REPLY_ENABLED`:
- Financial complaints (`বেতন পাইনি`, `পেমেন্ট সমস্যা`) — complaint-phrase guard
- Advance requests (`অ্যাডভান্স চাই`) — advance-request guard
- Contacts in `DRAFT_ALWAYS_ROLES` / `DRAFT_ALWAYS_PHONES` / `DRAFT_NAME_PREFIXES`
- `contact_risk_levels = admin_review_only` contacts
- All other unclassified or financial intents

---

## 2. Architecture

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                          INBOUND SOURCES                                     │
│                                                                              │
│  Bridge1 SQLite ──────┐                                                      │
│  (HR / 8801958122300) │                                                      │
│                        ├──► bridge_poller ──► process_message()             │
│  Bridge2 SQLite ──────┘    (5s poll loop)      (message_router)             │
│  (OPS/8801880446111)                           │                            │
│                                                 ▼                            │
│  Meta Webhook ─────────────────────────────► FastAPI /webhook               │
│  (8801880446111 Cloud)                                                       │
└──────────────────────────────────────────────────────────────────────────────┘
                                    │
                    ┌───────────────▼───────────────┐
                    │       ROUTING PIPELINE         │
                    │                                │
                    │  1. Silent-skip check          │
                    │  2. detect_identity()          │
                    │  3. classify_intent()          │
                    │  4. Route by role/intent       │
                    │     ├─ admin commands          │
                    │     ├─ escort lifecycle        │
                    │     ├─ recruitment funnel      │
                    │     ├─ payroll / FPE           │
                    │     ├─ attendance              │
                    │     ├─ KB lookup               │
                    │     ├─ Reviewed Memory lookup  │
                    │     └─ Ollama AI fallback      │
                    │  5. Draft-gate check           │
                    │  6. Auto-send or save draft    │
                    └───────────────┬───────────────┘
                                    │
              ┌─────────────────────┴──────────────────────┐
              │                                             │
    ┌─────────▼────────┐                         ┌────────▼────────┐
    │  fazle_draft_    │                         │  BridgeClient   │
    │  replies (PG)    │                         │  .send_strict() │
    │  (manual review) │                         │  Bridge1/2 API  │
    └──────────────────┘                         └─────────────────┘

SUPPORTING SERVICES
  PostgreSQL ←──── asyncpg pool (all state, queues, drafts, payroll)
  Redis      ←──── sessions, rate-limit cursors
  Ollama     ←──── qwen2.5:3b (reply generation)
  Whisper    ←──── speech-to-text (voice notes, model: small)
  Tesseract  ←──── OCR (release slips, PDFs)
```

---

## 3. Services & Ports

| Service | systemd unit | Port | Purpose |
|---|---|---|---|
| Fazle Core | `fazle-core.service` | 8200 | Main FastAPI app |
| WhatsApp Bridge 1 | `whatsapp-bridge.service` | 8082 | HR number (8801958122300) |
| WhatsApp Bridge 2 | `whatsapp-bridge2.service` | 8081 | OPS number (8801880446111) |
| Media Processor | `media-processor.service` | 8090 | Whisper STT + OCR |
| Social Auto-Reply | `fazle-social-auto-reply.service` | — | Messenger/FB comments |
| Fazle Agent | `fazle-agent.service` | — | Super agent (dry-run) |

All services are enabled and restart automatically (`Restart=always`, `RestartSec=10`).

**Health endpoint:** `GET http://localhost:8200/health`

Sample healthy response fields:
```json
{
  "status": "ok|degraded",
  "probes": {
    "db": {"status": "ok"},
    "bridge_poller_b1": {"status": "ok", "age_s": 5},
    "bridge_poller_b2": {"status": "ok", "age_s": 3},
    "outbound": {"status": "ok", "pending": 0, "dlq": 0},
    "ollama": {"status": "ok", "models": ["qwen2.5:3b"]},
    "mem": {"status": "ok", "available_mb": 15882},
    "disk": {"status": "ok|degraded", "used_pct": 81}
  }
}
```

---

## 4. WhatsApp Bridges

### Bridge 1 — HR (primary inbound)
- **Number:** 8801958122300
- **Label:** HR
- **Port:** 8082 (`BRIDGE1_URL=http://localhost:8082`)
- **Binary:** `/home/azim/whatsapp-mcp/whatsapp-bridge/whatsapp-bridge`
- **Store:** `/home/azim/whatsapp1/store/` (messages.db + whatsapp.db)
- **Logs:** `/home/azim/whatsapp-mcp/logs/bridge1.log`

### Bridge 2 — OPS (escort / operations)
- **Number:** 8801880446111
- **Label:** OPS
- **Port:** 8081 (`BRIDGE2_URL=http://localhost:8081`)
- **Store:** `/home/azim/whatsapp2/store/` (messages.db + whatsapp.db)
- **Logs:** `/home/azim/whatsapp-mcp/logs/bridge2.log`

### Bridge API Endpoints (both bridges)
- `POST /api/send` — send a message `{"recipient": jid, "message": text}`
- `POST /api/send-control` `{"allow": true/false}` — gate outbound sends
- `GET /api/send-status` — current gate state

### Send-Gate Recovery

The bridge send-gate (`/api/send-control`) defaults to **BLOCKED** on every bridge restart. `bridge_poller` calls `ensure_enabled()` on both bridges every 300 seconds (`_SEND_GATE_CHECK_INTERVAL`) to re-enable automatically. Log evidence: `[BR1] send-control ensure: allowed=True`.

### Re-QR a Bridge (after session drop)

```bash
sudo systemctl restart whatsapp-bridge.service
tail -f /home/azim/whatsapp-mcp/logs/bridge1.log | grep -A 5 "QR\|qr"
```

Scan the QR code with phone 8801958122300. For Bridge 2, use `whatsapp-bridge2.service` and phone 8801880446111.

### Meta WhatsApp Cloud API
- **Phone number ID:** 1190102727510644
- **WABA ID:** 3119866501549616
- **Verify token:** `fazle_core_webhook_2026`
- **Webhook:** `POST /webhook/meta` on port 8200
- **Admin number:** 8801880446111

---

## 5. Message Routing Pipeline

Every inbound message (from any source) flows through `modules/message_router/__init__.py:process_message()`.

### Step-by-Step

```
1. SILENT SKIP CHECK
   └─ accountant phone match → return ("", None)
   └─ display_name contains 'al-aqsa'|'escort'|'client' → return ("", None)

2. IDENTITY DETECTION  (modules/identity_brain)
   └─ resolve phone → role, name, employee record

3. ROUTING BY ROLE (priority order):
   ├─ family              → personal safe reply
   ├─ escort_roles        → handle_escort_client_message() → draft to admin
   ├─ admin               → process_admin_command() | list_payment_drafts()
   ├─ supervisor          → attendance_parser → KB → Reviewed Memory → AI
   ├─ accountant          → kb_get_reply → Reviewed Memory → AI fallback
   ├─ candidate           → recruit_intake() → funnel
   ├─ employee            → verification → slip/advance/salary/attendance
   ├─ repeat_client/vip   → kb_get_reply → Reviewed Memory → AI fallback
   └─ unknown             → classify_intent → KB → Reviewed Memory → AI

   Generic reply chain (steps 12–15 in process_message):
     12. Office location fast path (office_location intent → KB only, no AI)
     13. KB exact match   (fazle_knowledge_base + _FALLBACK)
     14. Reviewed Memory  (fazle_reviewed_replies — admin-approved replies)
     15. AI fallback      (Ollama / qwen2.5:3b)

4. DRAFT-GATE CHECK  (bridge_poller)
   ├─ AUTO_REPLY_ENABLED=true  → auto-send for approved intents (see §18)
   ├─ role in DRAFT_ALWAYS_ROLES → draft (unless safe intent)
   ├─ phone in DRAFT_ALWAYS_PHONES → draft (unless safe intent)
   ├─ display_name prefix in DRAFT_NAME_PREFIXES → draft (unless safe intent)
   ├─ financial intent hit (non-safe) → draft
   └─ safe intent (salary_query, payment_due, advance_request, recruitment) → autosend

5. SEND OR DRAFT
   └─ autosend → BridgeClient.send_strict() → appends 🤖 suffix
   └─ draft    → fazle_draft_replies (pending, awaiting admin APPROVE/REJECT)
```

### Intent Classification (`modules/intent`)

The `classify()` function returns an intent string. Financial keywords (`payment`, `salary`, `advance`, `dispute`, `hisab`, `বেতন`, `পেমেন্ট`, `অগ্রিম`, etc.) trigger `_FINANCIAL_DRAFT_INTENTS` forcing a draft, unless `_is_safe_autosend_intent()` returns True.

### Safe Auto-Send Intents

Whitelisted for auto-send even for draft-always contacts:
- `salary_query`
- `payment_due`
- `advance_request`
- `recruitment`

**NOT safe** (always draft): `employee_salary_complaint`, `payment_issue`, `legal_issue`, release-slip estimates, transport rate calculations.

---

## 6. Module Index

| Module | Purpose |
|---|---|
| `bridge_poller` | Polls bridge SQLite stores every 5s, drives routing |
| `message_router` | Unified routing logic, returns (reply, admin_notif) |
| `identity_brain` | Contact identity resolution (role, name, employee) |
| `intent` | Intent classification from message text |
| `knowledge_base` | RAG knowledge base lookup |
| `rag` | Retrieval-augmented generation pipeline |
| `escort` | Escort client order handling |
| `escort_lifecycle` | Release slip parsing, transport estimates, draft builder |
| `escort_roster` | Escort guard roster management (FastAPI router) |
| `escort_slip_extractor` | OCR + structured extraction from release slip images |
| `recruitment_flow` | Recruitment intake sessions, candidate funnel |
| `admin_commands` | APPROVE / REJECT / PAID / ADVANCE / ESCORTCONFIRM commands |
| `admin_commands.nl_router` | Natural-language admin queries (last_contact, chat_history) |
| `attendance` | Attendance message handling |
| `attendance_parser` | Supervisor attendance sheet parsing |
| `payment_workflow` | Escort payment drafts, advance request drafts |
| `payment_ingest` | Ingest payment confirmation messages |
| `payment_correction` | FPE payment correction commands |
| `payroll` | Payroll summary generation |
| `payroll_logic` | Payroll calculation helpers |
| `fazle_payroll_engine` | FPE: cash/income recording via WhatsApp commands |
| `outbound` | Social reply queue sweep (send_queue.sweep_once) |
| `scheduler` | Background tasks: daily digest, DLQ alerts, gap detection, backup |
| `social_auto_reply` | Facebook/Messenger/comment auto-reply pipeline |
| `draft_quality` | Reply quality gate (length, confidence, uncertain-intent) |
| `context_memory` | Per-contact conversation context for AI |
| `conversation_layer` | Shadow conversation capture |
| `contact_sync` | wbom_contacts sync from bridge address books |
| `number_identity` | Phone number normalization utilities |
| `media` | Media download helpers |
| `media_normalization` | Normalize media attachments for archive |
| `voice_processor` | Route voice notes to Whisper STT |
| `ocr_processor` | Tesseract OCR (images, PDFs) |
| `image_hash` | Perceptual hash for dedup |
| `message_archive` | Long-term message archive table |
| `reply_templates` | Canned reply helpers |
| `reviewed_reply_memory` | Active reply source — admin-edited drafts stored and served between KB and AI fallback (wired Phase 1) |
| `reports` | Daily/weekly digest reports |
| `observability` | Metrics, structured log aggregation |
| `backup` | PostgreSQL dump and rotation |
| `gap_detector` | Detects message gaps / missed replies |
| `gap_actions` | Actions on detected gaps |
| `rbac` | Role-based access control |
| `user_role` | User role management |
| `employee_utils` | Employee lookup helpers |
| `employee_verification` | Step-by-step identity verification sessions |
| `accountant_summary` | Accountant daily summary generation |
| `csv_import` | Bulk CSV import for contacts/employees |
| `admin_employees` | Admin API router for employee management |
| `admin_transactions` | Admin API router for transaction management |

---

## 7. Database Tables

All tables live in the `postgres` database. Connection via asyncpg pool (min 2 / max 10 connections, 30s command timeout).

### Core Tables

| Table | Purpose |
|---|---|
| `wbom_contacts` | Contact book (display_name, whatsapp_number, role, is_active) |
| `wbom_messages` | Inbound message archive |
| `wbom_whatsapp_messages` | Bridge-specific raw message store (dedup key) |
| `fazle_draft_replies` | Pending drafts awaiting admin APPROVE/REJECT |
| `social_reply_queue` | Outbound queue for auto-send messages |
| `processed_bridge_messages` | Dedup table keyed by (message_id, bridge) |
| `bridge_poller_cursor` | Per-bridge timestamp cursor (survives restarts) |
| `processed_outgoing_escort_messages` | Dedup for outgoing escort confirmations |
| `outbound_safety_incidents` | Log of blocked/circuit-open outbound attempts |

### Payroll / Escort

| Table | Purpose |
|---|---|
| `employees` | Employee records (phone, name, role, salary) |
| `fpe_transactions` | Cash/income transactions recorded by FPE |
| `escort_orders` | Active escort orders (client → guard assignment) |
| `escort_history` | Completed escort order archive |
| `escort_roster` | Guard availability roster |
| `escort_calculation_config` | Transport rate config (**NOT used by code** — see note below) |
| `fazle_recruitment_sessions` | Active recruitment intake sessions |
| `fazle_knowledge_base` | RAG knowledge base entries |
| `fazle_reviewed_replies` | Admin-approved reply memory — served between KB and AI fallback |

> **Note:** `escort_calculation_config` contains transport rates that differ from the hardcoded `_TRANSPORT_RATES` in `escort_lifecycle/__init__.py`. Code uses the hardcoded values. All release-slip estimates remain draft-only until this is reconciled.

### Queue Status Values (`social_reply_queue.status`)

| Status | Meaning |
|---|---|
| `pending` | Awaiting first send attempt |
| `sending` | Currently in-flight (reset to `dlq` if stuck after crash) |
| `sent` | Successfully delivered |
| `failed` | Failed, eligible for retry by `sweep_once()` |
| `dlq` | Dead-letter — excluded from automatic retry |
| `blocked` | Blocked by safety gate |

`sweep_once()` retries rows with `status IN ('pending', 'failed')` only. `dlq` rows are never retried automatically.

### Draft Status Values (`fazle_draft_replies.status`)

| Status | Meaning |
|---|---|
| `pending` | Awaiting admin review |
| `approved` / `sent` | Admin approved and delivered |
| `rejected` | Admin rejected |
| `expired` | TTL exceeded (24h default) |

---

## 8. Key Configuration

Configuration is loaded from `/home/azim/core/.env` via systemd `EnvironmentFile` — pydantic-settings reads from the process environment. Cached by `@lru_cache` — changes require a service restart.

> **Note:** `app/config.py` contains a stale `env_file` path (`/home/azim/fazle-core/.env`) that no longer exists; it is ignored at runtime because systemd pre-loads all variables before the process starts.

### Core Settings

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | — | asyncpg PostgreSQL URL |
| `REDIS_URL` | `redis://localhost:6379/9` | Redis connection |
| `OLLAMA_URL` | `http://localhost:11434` | Ollama API |
| `OLLAMA_MODEL` | `qwen2.5:3b` | LLM model |
| `APP_PORT` | `8200` | FastAPI listen port |
| `LOG_LEVEL` | `INFO` | Python log level |
| `INTERNAL_API_KEY` | — | Header auth for internal endpoints |

### Bridge Settings

| Variable | Value | Description |
|---|---|---|
| `BRIDGE1_URL` | `http://localhost:8082` | Bridge 1 API base |
| `BRIDGE1_NUMBER` | `8801958122300` | HR WhatsApp number |
| `BRIDGE1_LABEL` | `HR` | Log label |
| `BRIDGE2_URL` | `http://localhost:8081` | Bridge 2 API base |
| `BRIDGE2_NUMBER` | `8801880446111` | OPS WhatsApp number |
| `BRIDGE2_LABEL` | `OPS` | Log label |
| `OUTBOUND_BRIDGE_TIMEOUT_S` | `10` | HTTP timeout for bridge send calls |

### Auto-Reply Settings

| Variable | Current | Description |
|---|---|---|
| `AUTO_REPLY_ENABLED` | `false` | Master auto-reply switch |
| `RECRUITMENT_AUTOREPLY_ENABLED` | `true` | Recruitment intake bypass |
| `AUTO_REPLY_SOURCES` | `bridge1,bridge2` | Sources eligible for auto-reply |
| `DRAFT_CREATION_ENABLED` | `false` | Create drafts from inbound processing |
| `OUTBOUND_ENABLED` | `false` | Enable outbound queue sweep |
| `USE_OUTBOUND_QUEUE` | `true` | Route sends through queue |
| `OUTBOUND_SWEEP_INTERVAL_S` | `10` | How often sweep_once() runs |

### Draft-Gate Settings

| Variable | Current | Description |
|---|---|---|
| `DRAFT_ALWAYS_ROLES` | `accountant,client_escort_buyer,vip_client,...` | Roles always drafted |
| `DRAFT_ALWAYS_PHONES` | (15 phones) | Explicit phones always drafted |
| `DRAFT_ALWAYS_NAMES` | `Jakir Master,Shohag Security` | Name substrings always drafted |
| `DRAFT_NAME_PREFIXES` | `client,escort,office` | Name prefix → always drafted |
| `DRAFT_TTL_HOURS` | `24` | Hours before pending draft expires |
| `DRAFT_QUALITY_GATE` | `true` | Enable reply quality filtering |
| `AI_SAFE_MODE` | `false` | Draft uncertain/long AI replies |
| `REVIEWED_REPLY_MEMORY_ENABLED` | `true` | Persist admin-edited drafts; serve between KB and AI fallback |

### Company Settings

| Variable | Value | Description |
|---|---|---|
| `COMPANY_NAME` | `Al-Aqsa Security & Logistics Services Ltd.` | |
| `ACCOUNTANT_PHONE` | `8801844836824` | Silent-skip phone (no reply, no draft) |
| `ADMIN_NUMBERS` | `8801880446111,8801958122300` | Admin command senders |
| `CONTACT_RISK_LEVELS` | `8801631422559:admin_review_only` | Per-contact risk override |

---

## 9. Auto-Reply & Draft Policy

### Current Mode: Production Active

`AUTO_REPLY_ENABLED=true` — activated 2026-05-29 (Phase 4.5). Auto-send is live for approved intents. Protected intents always route to draft regardless of this flag.

See **§18 Production Auto-Reply Policy** for the complete routing rules.

### To Enable Auto-Reply (when ready)

```bash
sed -i 's/^AUTO_REPLY_ENABLED=false/AUTO_REPLY_ENABLED=true/' /home/azim/core/.env
sed -i 's/^OUTBOUND_ENABLED=false/OUTBOUND_ENABLED=true/' /home/azim/core/.env
sudo systemctl restart fazle-core.service
```

### Draft-Always Contacts

Even with `AUTO_REPLY_ENABLED=true`, these contacts always produce drafts (not auto-sends), unless the intent is in the safe auto-send whitelist:

1. **Role match** — `DRAFT_ALWAYS_ROLES`
2. **Phone match** — `DRAFT_ALWAYS_PHONES` (explicit 15-phone list)
3. **Name substring** — `DRAFT_ALWAYS_NAMES` (case-insensitive)
4. **Name prefix** — `DRAFT_NAME_PREFIXES`: client, escort, office

### Safe Auto-Send Override

When intent is `salary_query`, `payment_due`, `advance_request`, or `recruitment`, the draft-always and financial-draft gates are both bypassed by `_is_safe_autosend_intent()`.

**Never auto-send** (always draft regardless):
- `employee_salary_complaint`
- `legal_issue` / `payment_issue`
- Release slip transport estimates (transport rates hardcoded, not DB-synced)

### Automated Reply Suffix

Every auto-sent message has this appended exactly once (double-append protected by checking for anchor `🤖 Automated Reply System`):

```
─────────────────
🤖 Automated Reply System
এই বার্তাটি স্বয়ংক্রিয়ভাবে তৈরি হয়েছে। ভুল হতে পারে।
```

For multi-part messages (`send_multi()`), the suffix is appended to the last segment only.

---

## 10. Contact Exclusion & Silent Skip

Implemented in `modules/message_router/_should_silent_skip()`.

### Rules

Any inbound message from a matching contact returns `("", None)` — no reply, no draft, no queue entry.

**Rule 1 — Accountant phone:**
```
sender == ACCOUNTANT_PHONE (8801844836824)
```

**Rule 2 — Display name token:**
`wbom_contacts.display_name` (case-insensitive) contains any of: `al-aqsa`, `escort`, `client`

Both `01XXXXXXXXX` and `880XXXXXXXXX` phone formats are checked via `_phone_variants()`.

### Why Silent Skip?

Internal contacts (escort coordinators, office accounts) whose names contain these tokens are operational accounts — not inbound customers. They must not trigger the AI reply pipeline.

---

## 11. Outbound Queue & Circuit Breaker

### Outbound Queue (`social_reply_queue`)

`modules/outbound/send_queue.py:sweep_once()` runs every 10s and retries up to 5 rows with `status IN ('pending', 'failed')`:

```
sweep_once()
  → fetch pending/failed rows (limit 5)
  → BridgeClient.send_strict(recipient_id, text)
    → on success: status='sent', sent_at=NOW()
    → on failure: status='failed', attempts++, next_retry_at=NOW()+backoff
    → on max attempts: status='dlq'
```

### Circuit Breaker (`app/bridge.py:CircuitBreaker`)

Per-bridge state machine:

```
CLOSED  ──[5 failures in 60s]──► OPEN  ──[after 60s]──► HALF_OPEN
  ▲                                                           │
  └──────────[probe success]─────────────────────────────────┘
                                        │[probe failure]
                                        ▼
                                      OPEN (re-armed)
```

Parameters: failure_threshold=5, window_seconds=60, open_seconds=60. HALF_OPEN allows exactly one probe request.

### Fix Stuck `sending` Rows

```sql
UPDATE social_reply_queue
SET status='dlq', attempts=10, next_retry_at=NOW() + INTERVAL '999 days'
WHERE status='sending';
```

---

## 12. Media Processing (STT / OCR)

**Service:** `media-processor.service` on port 8090
**Script:** `/home/azim/shared/media/media-processor/server.py`
**Logs:** `/home/azim/whatsapp-mcp/logs/media-processor.log`

### Whisper (Speech-to-Text)

- **Model:** `small` (244 MB) — upgraded from `base` (74 MB) on 2026-05-27
- **Env var:** `WHISPER_MODEL=small` in `/etc/systemd/system/media-processor.service`
- Voice notes routed via `modules/voice_processor` → `POST http://localhost:8090/transcribe`
- Transcription result used as message text for routing

Verify:
```bash
grep "WHISPER_MODEL" /etc/systemd/system/media-processor.service
```

### OCR (Tesseract)

- Engine: Tesseract via `modules/ocr_processor`
- Use cases: release slip images, PDF attachments
- Concurrency: `OCR_CONCURRENCY=2`
- Returns confidence score 0-100 with each extraction

### Release Slip Processing

1. Employee sends image/PDF via WhatsApp
2. OCR extracts: vessel name, location, duty dates, guard name
3. `escort_lifecycle.build_release_draft()` calculates transport estimate using `_TRANSPORT_RATES`
4. Draft saved — **NEVER auto-sent** (transport rates hardcoded, not DB-synced)

**Transport rates — current state:**

Operational transport values are maintained through management-approved policy files.
Authoritative source: **`resources/ops/transport_allowances.txt`**

| Destination group | Code `_TRANSPORT_RATES` (current) | DB `escort_calculation_config` (current) | Authoritative (`ops/`) |
|---|---|---|---|
| Dhaka / Narayanganj / Bhairab / Ashuganj / Kaliganj / Rupganj group | 600 | 600 (Narayanganj) / 800 (Bhairab, Ashuganj) | **600** |
| Faridpur | 800 | not in DB | **700** |
| Mongla | 600 *(default — not in table)* | 1500 | **700** |
| Barishal / Jhalokathi / Bhola / Nagarbari group | 900 | 1200 (Barishal) / 900 (Nagarbari) | **900** |
| Noapara / Jessore / Khulna group | 1000 | 1000 | **1000** |

Code `_TRANSPORT_RATES` and DB `escort_calculation_config` are pending Phase 3A (DB) and Phase 3B (Python) alignment.
Until aligned, all release-slip estimates remain **DRAFT-ONLY** — admin review before any payment.

---

## 13. Fazle Payroll Engine (FPE)

Records cash and income transactions via WhatsApp commands. Runs as background tasks within `fazle-core.service`.

### Authorized Commands

**Cash recording** (phones in `FPE_CASH_AUTHORIZED_PHONES`):
```
Cash <phone> <name> <amount>
```
Employee must already exist. Replies with error on failure.

**Income recording** (phones in `FPE_INCOME_AUTHORIZED_PHONES`):
```
Income <phone> <name> <amount>
```
Auto-creates employee if missing.

### Historical Sync

On startup, FPE syncs historical DM JIDs from `FPE_SYNC_CHAT_JIDS` (3 JIDs configured) to backfill transaction history from bridge chat logs.

### FPE API Routes (mounted at `/fpe/`)

- `GET /fpe/employees` — list employees
- `GET /fpe/transactions` — list transactions
- `POST /fpe/correct` — transaction correction

---

## 14. Admin Commands

Sent via WhatsApp from an authorized admin number in `ADMIN_NUMBERS`.

### Structured Commands

| Command | Action |
|---|---|
| `APPROVE <draft_id>` | Approve draft, send to recipient via correct bridge |
| `APPROVE <id> <id> ...` | Batch approve multiple drafts in one command |
| `REJECT <draft_id> [reason]` | Reject draft, notify recipient |
| `EDIT <draft_id> <new text>` | Edit draft reply text; corrected reply stored in reviewed memory for future reuse |
| `PAID <phone> <amount>` | Mark payment received |
| `ADVANCE <phone> <amount>` | Record advance payment |
| `ESCORTCONFIRM <order_id>` | Confirm escort order completion |
| `LIST` | List pending payment drafts |

### Natural Language Queries (`nl_router`)

Admin can ask in plain text:
- "last contact from [name/phone]"
- "show chat history for [phone]"
- "pending drafts"
- "recruitment stats"

### Admin Approval Flow

```
Admin: APPROVE 42
  → admin_commands._cmd_approve(42)
  → fetch from fazle_draft_replies
  → send via correct bridge (based on draft source)
  → status='sent', sent_at=NOW()
  → notify admin of result

Admin: EDIT 42 <corrected text>
  → admin_commands._cmd_edit(42, text)
  → UPDATE fazle_draft_replies SET reply_text=..., status='edited'
  → reviewed_reply_memory.create_or_update_from_edit()
      → quality gate check (reject LLM fallback strings, path leakage, admin commands)
      → INSERT/UPDATE fazle_reviewed_replies
      → future identical intent+role queries return this reply before AI fallback
```

Approval bypasses `AUTO_REPLY_ENABLED` — admin approval is the explicit send decision.

EDIT stores the corrected reply in `fazle_reviewed_replies` for future reuse. Reviewed replies are served at routing step 13, between KB (step 12) and AI (step 14).

---

## 15. Operations Runbook

### Service Status

```bash
sudo systemctl status fazle-core whatsapp-bridge whatsapp-bridge2 media-processor fazle-social-auto-reply
```

### Restart Services

```bash
sudo systemctl restart fazle-core.service
sudo systemctl restart whatsapp-bridge.service    # Bridge 1
sudo systemctl restart whatsapp-bridge2.service   # Bridge 2
sudo systemctl restart media-processor.service
```

### Live Logs

```bash
# Fazle Core application
tail -f /home/azim/fazle-core/logs/fazle-core.log

# Bridge 1
tail -f /home/azim/whatsapp-mcp/logs/bridge1.log

# Bridge 2
tail -f /home/azim/whatsapp-mcp/logs/bridge2.log

# Media processor (Whisper/OCR)
tail -f /home/azim/whatsapp-mcp/logs/media-processor.log
```

### Health Check

```bash
curl -s http://localhost:8200/health | python3 -m json.tool
```

### Check Bridge Send Gates

```bash
curl -s http://localhost:8082/api/send-status   # Bridge 1 (should show allowed=true)
curl -s http://localhost:8081/api/send-status   # Bridge 2 (should show allowed=true)
```

If blocked: restart fazle-core (calls `ensure_enabled()` at startup and every 5 minutes automatically).

### Re-QR Bridge After Session Drop

```bash
# Bridge 1 (number: 8801958122300)
sudo systemctl restart whatsapp-bridge.service
tail -f /home/azim/whatsapp-mcp/logs/bridge1.log | grep -A 20 "QR"

# Bridge 2 (number: 8801880446111)
sudo systemctl restart whatsapp-bridge2.service
tail -f /home/azim/whatsapp-mcp/logs/bridge2.log | grep -A 20 "QR"
```

### Fix Stuck Queue Rows

```bash
# Move all stuck 'sending' rows to dlq
docker exec -it ai-postgres psql -U postgres -d postgres -c \
  "UPDATE social_reply_queue SET status='dlq', attempts=10 WHERE status='sending';"
```

### View Pending Drafts

```bash
docker exec -it ai-postgres psql -U postgres -d postgres -c \
  "SELECT id, sender_phone, intent, created_at FROM fazle_draft_replies
   WHERE status='pending' ORDER BY created_at DESC LIMIT 20;"
```

### Enable Auto-Reply (when ready)

```bash
# 1. Edit .env
nano /home/azim/core/.env
# Set: AUTO_REPLY_ENABLED=true
# Set: OUTBOUND_ENABLED=true

# 2. Restart
sudo systemctl restart fazle-core.service

# 3. Confirm
curl -s http://localhost:8200/health | python3 -m json.tool
```

### Disable Auto-Reply (emergency rollback)

```bash
sed -i 's/^AUTO_REPLY_ENABLED=true/AUTO_REPLY_ENABLED=false/' /home/azim/core/.env
sed -i 's/^OUTBOUND_ENABLED=true/OUTBOUND_ENABLED=false/' /home/azim/core/.env
sudo systemctl restart fazle-core.service
```

### Disk Usage

Health probe reports `disk.used_pct`. Warning: 80%, critical: 90%. Current: ~81%.

```bash
df -h /
du -sh /home/azim/backups/fazle/* 2>/dev/null | sort -rh | head -10
```

### Backup Verification

```bash
ls -lh /home/azim/backups/fazle/ | tail -10
# Should show backup files from within last 48 hours
```

---

## 16. Changelog & Update Policy

### Update Policy

- All `.env` changes require `sudo systemctl restart fazle-core.service` (settings are `@lru_cache`'d)
- Bridge send-gate re-enables automatically every 5 minutes — no manual action needed after bridge restarts
- Transport rates: `resources/ops/transport_allowances.txt` is the authoritative source. Both `escort_lifecycle._TRANSPORT_RATES` (code) and `escort_calculation_config` (DB) require Phase 3A/3B updates to match
- `AUTO_REPLY_ENABLED=false` is safe rollback at any time
- Never set a queue row directly from `dlq` → `pending` without understanding why it failed; investigate first

### Version History

| Date | Version | Changes |
|---|---|---|
| 2026-05-29 | v1.2.2-prod | Phase 6E: office_location intent — KB-only fast path, safe-autosend for all roles, 16 trigger keywords, wrong address entries deactivated |
| 2026-05-29 | v1.2.1-prod | Phase 4.5: Production auto-reply activated. _SAFE_AUTOSEND_INTENTS expanded (recruitment/join/greeting/salary_query/payment_due/attendance/leave/escort_duty). Complaint-phrase guard + advance-request guard added. README §18 added. |
| 2026-05-29 | v1.2.0-docs | README R1: company name, .env paths, reviewed memory wiring, EDIT command, transport table, knowledge hierarchy, known issues refresh |
| 2026-05-29 | v1.1.2-ops | Phase 2: 8 authoritative ops files (resources/ops/*), company identity, salary structure, transport allowances, joining fee, food expense, probation, attendance, release workflow |
| 2026-05-29 | v1.1.1-phase1 | Phase 1: reviewed reply memory wiring — EDIT→create_or_update, router lookup (KB→Reviewed→AI), safety filters, NameError bug fix |
| 2026-05-28 | v1.1.1-prod | This README: comprehensive production documentation |
| 2026-05-27 | v1.1.1-dev | 7 stabilization tasks: silent-skip, safe autosend policy, release-slip draft enforcement, automated reply suffix (🤖), send-gate periodic recovery (300s), Whisper upgrade base→small, DLQ fix for stuck sending rows |
| 2026-05-26 | v1.1.0-audit | Live audit: transport rate mismatch documented, `_FINANCIAL_DRAFT_INTENTS` safe-intent bypass (`_is_safe_autosend_intent`), Bridge1 re-QR after session drop |
| 2026-05-23 | v1.1.0 | Phase 1.1: admin natural-language query router (chat_history, last_contact) |
| ~2026-05-13 | v1.0.2 | Hotfix: ingest policy locked — DMs always persisted; groups/newsletters skipped at SQL level |
| ~2026-05-12 | v1.0.1 | Hotfix: draft quality gate, dedup, multi-ID admin, Bengali digits |
| ~2026-05-09 | v1.0.0 | GA: Batches B11→B24 — recruitment, payments, escort lifecycle, payroll, outbound resilience, scheduler, reports, backup, RBAC, dashboard, RAG, observability, CI |

### Known Issues & Next Steps

1. **Transport rates partially aligned** *(PARTIALLY RESOLVED)* — Management-confirmed rates are documented in `resources/ops/transport_allowances.txt`. Code `_TRANSPORT_RATES` in `escort_lifecycle/__init__.py` and DB `escort_calculation_config` are pending Phase 3A (DB) and Phase 3B (Python) updates. All release-slip estimates remain draft-only until code and DB are updated.
2. **DB knowledge entries need cleanup** *(OPEN)* — `fazle_knowledge_base` has stale entries: `company_name="Azim Technologies Ltd."`, `company_address="Akborsha"`, `b11_no_fee` returns "no joining fee" (contradicts policy). Phase 3A SQL required.
3. **Python fallback constants need cleanup** *(OPEN)* — `modules/knowledge_base/_FALLBACK` contains "no joining fee" wording and shows only scout salary (missing guard ₺17,000 package). Phase 3B Python update required.
4. **Disk at ~81%** — monitor usage; rotate old backups or expand volume if approaching 90%.
5. **Bridge SQLite mtime_age "degraded"** — health probe shows bridge DBs as degraded when no new messages arrive (stale mtime). Expected behavior during quiet periods, not an error.
6. **Auto-reply active** — `AUTO_REPLY_ENABLED=true` since 2026-05-29. Recruitment and employee-information intents auto-send. Financial complaints and advance requests are complaint-phrase guarded (always draft).
7. **`fazle_agent.service` in dry-run** — super agent service is running but in dry-run mode.
8. **`app/config.py` stale `env_file` path** *(LOW)* — `env_file = "/home/azim/fazle-core/.env"` points to a non-existent file. System operates correctly because systemd pre-loads env vars; the stale path is silently ignored by pydantic-settings.

---

## 17. AI Knowledge Hierarchy

Every AI-generated reply consults knowledge sources in strict priority order. A lower-priority source never overrides a higher-priority one.

| Priority | Source | Location | Status | Notes |
|---|---|---|---|---|
| **P1** | Authoritative ops files | `resources/ops/*.txt` | ✅ Active (8 files) | Management-confirmed truth. Company identity, salary, transport, joining fee, food, probation, attendance, release workflow. |
| **P2** | Employee policy document | `resources/employee_policy_rules_joining_form.txt` | ✅ Active | Official joining form PDF source. 22 sections covering all HR rules. |
| **P3** | Reviewed reply memory | `fazle_reviewed_replies` (DB) | ✅ Active (wired Phase 1) | Admin-edited draft replies stored and served at routing step 13, before AI fallback. |
| **P4** | Knowledge base entries | `fazle_knowledge_base` (DB) | ✅ Active — partial cleanup pending | Admin-curated entries. Some stale values (see Known Issues §2). Phase 3A cleanup required. |
| **P5** | Python fallback constants | `modules/knowledge_base/_FALLBACK` | ✅ Active — partial cleanup pending | Offline fallback when DB unavailable. Some stale values (see Known Issues §3). Phase 3B cleanup required. |
| **P6** | LLM generation | Ollama `qwen2.5:3b` | ✅ Active | Last resort. Grounded by P1/P2 RAG corpus via BM25 retrieval. |

**Rule:** When sources conflict, the lowest-numbered (highest-priority) source wins.

### Reply lookup execution order (`process_message()`)

```
Inbound message
  ├─ Role-specific handlers (admin, escort, recruitment, employee, etc.)
  │    └─ Exit early if handled
  │
  └─ Generic fallback chain (all remaining roles/intents):
       12. Knowledge Base lookup  — fazle_knowledge_base (DB) + _FALLBACK (Python)
       13. Reviewed Memory lookup — fazle_reviewed_replies (DB)
            └─ Cascading scope: intent+role+phone → intent+role → intent
       14. AI Fallback            — Ollama LLM, grounded by BM25 RAG over P1/P2 corpus
```

### RAG corpus (feeds into step 14 via BM25)

The `modules/rag` BM25 index is built from `resources/` text files. Current index: **13 files**.

| Directory | Files | Indexed |
|---|---|---|
| `resources/ops/` | 8 ops files (P1) | ✅ |
| `resources/` (root) | 5 active txt files (P2) | ✅ |
| `resources/_internal_archived/` | 3 files | ❌ Excluded by dir rule |
| `*.bak.*` files | 2 stale backups | ❌ Excluded by PATCH 3 |

### Authoritative topic routing

| Topic | Authoritative Source |
|---|---|
| Company name / address | `ops/company_identity.txt` |
| Guard salary (₺17,000 / ₺24,700) | `ops/salary_structure.txt` |
| Scout salary (₺10,000–18,000) | `ops/salary_structure.txt` |
| Candidate vs employee salary routing | `ops/salary_structure.txt` (AI routing rule) |
| Transport allowances | `ops/transport_allowances.txt` |
| Joining fee (₺330 form + ₺3,500) | `ops/joining_fee_policy.txt` |
| Food expense (₺150/day conditional) | `ops/food_expense_policy.txt` |
| Probation / training rules | `ops/probation_and_training.txt` |
| Attendance / deduction rules | `ops/attendance_rules.txt` |
| Release slip workflow / OCR fields | `ops/release_workflow.txt` |
| All other HR policy | `employee_policy_rules_joining_form.txt` |

---

## 18. Production Auto-Reply Policy

**Activated:** 2026-05-29 | `AUTO_REPLY_ENABLED=true`

This section is the single authoritative reference for what the system will and will not auto-send.

### Active Auto-Send Intents (`_SAFE_AUTOSEND_INTENTS`)

| Intent | Classifier keyword examples | Typical query |
|---|---|---|
| `recruitment` | চাকরি, job, vacancy, আবেদন | "চাকরি আছে?", "কাজ করতে চাই" |
| `join` | joining, join, যোগদান, জয়েন, ভর্তি হব | "কিভাবে জয়েন করবো?", "ভর্তি হতে কী লাগবে?" |
| `greeting` | সালাম, hello, menu, start | "সালাম", "hi", "menu" |
| `office_location` | অফিস কোথায়, কোথায় অফিস, office address, victoria gate | "অফিস কোথায়?", "হেড অফিস কই?", "কোথায় যেতে হবে?" |
| `salary_query` | বেতন, salary, কত পাব | "বেতন কত?", "আমার বেতন কবে হবে?" |
| `payment_due` | টাকা কবে, পাওনা, খাবার ভাতা | "বেতন কখন পাব?", "খাবার ভাতা কিভাবে হিসাব হয়?" |
| `attendance` | হাজিরা, attendance, উপস্থিত | "হাজিরা নিয়ম কী?", "কতদিন অনুপস্থিত থাকা যাবে?" |
| `leave` | ছুটি, leave, পদত্যাগ | "ছুটির নিয়ম কী?", "পদত্যাগ করতে কী লাগবে?" |
| `escort_duty` | ডিউটি, duty, transport | "আমি কতদিন ডিউটি করেছি?", "যাতায়াত ভাতা কত?" |

**Source:** `modules/message_router/__init__.py` → `_SAFE_AUTOSEND_INTENTS`

---

### Protection Layers (applied in order, before auto-send)

```
Layer 0: Role / phone / name exclusions (_is_draft_always)
  DRAFT_ALWAYS_ROLES    = accountant, client_escort_buyer, vip_client, repeat_client
  DRAFT_ALWAYS_PHONES   = 15 explicit phones
  DRAFT_ALWAYS_NAMES    = Jakir Master, Shohag Security
  DRAFT_NAME_PREFIXES   = client, escort, office
  contact_risk_levels   = admin_review_only → always draft
  → Contacts matching ANY of these rules are ALWAYS drafted, regardless of intent.
  → Exception: safe intents still override DRAFT_ALWAYS role/name/prefix gates.

Layer 1: Advance-request text guard (_ADVANCE_REQUEST_PHRASES)
  Triggers: "অ্যাডভান্স চাই", "অগ্রিম দরকার", "advance লাগবে", etc.
  → DRAFT — regardless of intent, regardless of contact role.

Layer 2: Financial intent gate (_FINANCIAL_DRAFT_INTENTS)
  Tokens: payment, salary, advance, dispute, বেতন, পেমেন্ট, অগ্রিম, etc.
  If intent contains these AND is NOT in _SAFE_AUTOSEND_INTENTS → DRAFT.

Layer 3: Complaint-phrase override (_COMPLAINT_PHRASES)
  Triggers: "পাইনি", "সমস্যা", "হয়নি", "দেয়নি", "কম এসেছে", "ভুল হিসাব",
            "অভিযোগ", "বেতন মেরে", "dispute", "issue", "problem", etc.
  If financial intent AND text contains any complaint phrase → DRAFT
  (overrides safe-autosend classification for that message).

Layer 4: Additional safety gates
  Loop detection     : 5+ replies/60s for same phone → autoreply paused
  Prompt injection   : detected → quarantined, not routed to LLM
  Draft quality gate : DRAFT_QUALITY_GATE=true — poor LLM output → draft
  Outbound poison    : LLM analysis/chain-of-thought text → blocked
  Circuit breaker    : bridge failure → open state → no sends
```

**Source:** `modules/bridge_poller/__init__.py` draft-gate block

---

### Protected Flows — Never Auto-Send

| Flow | Protection mechanism |
|---|---|
| Salary complaints (`বেতন পাইনি`, `বেতন ভুল`) | Layer 3 complaint-phrase override |
| Payment disputes (`পেমেন্ট সমস্যা`, `টাকা কম`) | Layer 3 complaint-phrase override |
| Advance requests (`অ্যাডভান্স চাই`, `অগ্রিম দরকার`) | Layer 1 advance-request guard |
| Release-slip payment settlement | `build_release_draft()` always creates draft |
| Accountant commands | `accountant` role in DRAFT_ALWAYS_ROLES → Layer 0 |
| Client / escort buyer orders | `client_escort_buyer`, `vip_client` → Layer 0 |
| Admin commands | Admin role exits router before auto-send path |
| APPROVE / REJECT / PAID | Admin command handler — direct send, not auto-send pipeline |
| Legal / payment issues | `complaint` intent not in `_SAFE_AUTOSEND_INTENTS` |

---

### Automated Reply Disclaimer

Every auto-sent message receives exactly one disclaimer, appended by `BridgeClient.send_strict()`:

```
─────────────────
🤖 Automated Reply System
এই বার্তাটি স্বয়ংক্রিয়ভাবে তৈরি হয়েছে। ভুল হতে পারে।
```

**Location:** `app/bridge.py` — `_AUTOMATED_SUFFIX` constant, lines 15–20.
Double-append protected by `_AUTOMATED_SUFFIX_ANCHOR` check.
For multi-part messages: appended to last segment only (`send_multi()`).

---

### Rollback

To disable auto-reply immediately:

```bash
sed -i 's/^AUTO_REPLY_ENABLED=true/AUTO_REPLY_ENABLED=false/' /home/azim/core/.env
sudo systemctl restart fazle-core.service
```

Zero data loss. All messages revert to draft mode. The complaint guards and advance-request guards remain in code as passive safety nets — they do not need reverting.
