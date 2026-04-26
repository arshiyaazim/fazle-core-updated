# Fazle Core — WhatsApp AI Operations Engine

**Version:** v1.0 (B11 → B24 complete) · **Status:** ready for launch
· **Repo path:** `/home/azim/fazle-core` · **README path:** `/home/azim/fazle-core/README.md`

A production FastAPI backend running on VPS that bridges WhatsApp conversations into structured business workflows for HR, Payroll, and Escort Operations.

## Documentation

| Doc | Purpose |
|---|---|
| [docs/V1_LAUNCH_CHECKLIST.md](docs/V1_LAUNCH_CHECKLIST.md) | **Tick before flipping `AUTO_REPLY_ENABLED=true`** — security, backup, daily routine, owner dashboard, monetization |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | System diagram, components, data flow, modules |
| [docs/API.md](docs/API.md) | Every HTTP endpoint with auth + examples |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | Deploy, restart, backup, monitor, troubleshoot |
| [docs/ROADMAP.md](docs/ROADMAP.md) | Versioning model, batch ledger B11 → B24, candidate batches B25 → B34 |

## Overview

Fazle Core processes inbound WhatsApp messages from two bridges, classifies intent using a local LLM (Ollama/qwen2.5), and routes them into the appropriate workflow:

- **Escort Operations** — Slip extraction, payment draft creation, admin approval
- **HR / Recruitment** — Candidate tracking, duty records, payroll drafts
- **Admin Commands** — APPROVE / REJECT / EDIT / PAID / ADVANCE / STATUS via WhatsApp

## Architecture

```
WhatsApp → Bridge1 (HR, :8080)   ─┐
WhatsApp → Bridge2 (OPS, :8081)  ─┤→ fazle-core (:8200) → PostgreSQL
WhatsApp → Meta Webhook           ─┘         ↓
                                         Ollama (LLM)
```

## Key Features

- **SAFE MODE**: `AUTO_REPLY_ENABLED=false` — suppresses outbound messages for safe testing
- **Dual Bridge Poller**: polls both SQLite bridge DBs every 5s
- **Intent Classification**: local LLM classifies every message before routing
- **Admin Approval Loop**: drafts queued → admin approves via WhatsApp → sent
- **Knowledge Base**: 105+ company rules loaded into context

## Modules

| Module | Purpose |
|---|---|
| `bridge_poller` | Polls bridge1/bridge2 SQLite, routes messages |
| `message_router` | Central routing and admin command detection |
| `admin_commands` | APPROVE/REJECT/EDIT/PAID/ADVANCE/STATUS |
| `escort` | Escort slip parsing and payment draft creation |
| `recruitment` | Candidate workflow and duty tracking |
| `payment_workflow` | Payment finalization and accountant notification |
| `knowledge_base` | Company rules and policy lookup |
| `intent` | LLM-based message intent classification |
| `ollama` | Ollama API client with health check |

## Stack

- **Runtime**: Python 3.10, FastAPI, uvicorn
- **DB**: PostgreSQL 15 (asyncpg), SQLite (bridge stores)
- **LLM**: Ollama 0.3.14, model: `qwen2.5:1.5b`
- **Bridges**: WhatsApp bridge (Go binary) × 2
- **Infra**: Docker (Ollama, PostgreSQL, Redis), systemd services

## Running

```bash
# Production (systemd)
sudo systemctl status fazle-core.service

# Health check
curl http://localhost:8200/health

# Logs
tail -f /home/azim/fazle-core/logs/fazle-core.log
```

## Environment

See `.env.example` for required variables. Never commit `.env`.

## Branch Strategy

- `main` — production stable
- `develop` — active sprint work
- `hotfix/*` — emergency fixes
- `feature/*` — optional modules

## Safe Deployment

```bash
# Work on develop
git checkout develop
# ... changes ...
git commit -m "fix: description"

# Deploy to production
git checkout main
git merge develop
sudo systemctl restart fazle-core.service
```

## Versioning

Fazle Core ships as **immutable versions**. v1.0 is frozen for daily
use. Future features (B25 alerting, B26 multi-tenant, custom batches…)
are built in a **clone** of this folder on a different port, tested in
isolation, then promoted via the launch checklist:

```
/home/azim/fazle-core            ← live (v1.0)
/home/azim/fazle-core-v2-dev     ← work-in-progress copy on port 8201
/home/azim/fazle-core-v1-archive ← previous version, kept 30 days
```

Full workflow + bump rules: [docs/ROADMAP.md](docs/ROADMAP.md#versioning-model).

## Launching v1

1. Open [docs/V1_LAUNCH_CHECKLIST.md](docs/V1_LAUNCH_CHECKLIST.md).
2. Tick every box top-to-bottom (pre-flight → security → backup →
   daily routine → dashboard → monetization).
3. Flip `AUTO_REPLY_ENABLED=true` in `.env`, restart the service, tag
   `git tag -a v1.0`.
4. Watch `/observability/summary` and `journalctl -u fazle-core -f`
   for the first 48 hours.
