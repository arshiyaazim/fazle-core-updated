# Fazle Core — WhatsApp AI Operations Engine

A production FastAPI backend running on VPS that bridges WhatsApp conversations into structured business workflows for HR, Payroll, and Escort Operations.

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
