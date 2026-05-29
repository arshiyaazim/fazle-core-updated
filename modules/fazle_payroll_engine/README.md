# Fazle Payroll Engine (FPE)

The FPE is the core financial processing subsystem.  It handles payroll
calculations, OCR-based payslip parsing, payment ingestion, and database
migrations.

## Components

| Path | Purpose |
|------|---------|
| `__init__.py` | `start_fpe()` — runs all migrations on startup |
| `workers.py` | Payroll batch workers, OCR pipeline, WhatsApp sync |
| `normalizer.py` | `normalize_bd_phone()`, `normalize_name()` |
| `migrations/` | Ordered SQL migration files (auto-run at startup) |

## Migration System

All files matching `migrations/*.sql` are executed in alphabetical order
by `start_fpe()` at application startup.  Each statement is run
idempotently (`IF NOT EXISTS`, `ON CONFLICT DO NOTHING`).

| File | Purpose |
|------|---------|
| `001_fpe_schema.sql` | Core tables: employees, programs, drafts, transactions |
| `002–007` | Incremental schema additions |
| `008_unification.sql` | `wbom_employee_id`, `escort_roster_entry_id`, `fazle_processing_locks` |
| `009_stabilization.sql` | `expires_at` on drafts, `is_historical` flag, heartbeat table, message queue |

## Key Tables

| Table | Purpose |
|-------|---------|
| `wbom_employees` | Employee directory |
| `wbom_escort_programs` | Vessel escort duty records |
| `wbom_attendance` | Daily attendance |
| `wbom_cash_transactions` | Cash / bKash / Nagad transactions |
| `fazle_payment_drafts` | Pending payment drafts (admin approval queue) |
| `fazle_processing_locks` | Distributed lock table |
| `fazle_bridge_heartbeats` | Per-bridge liveness tracking |
| `fazle_message_queue` | Central inbound message queue |

## Adding a Migration

1. Create `migrations/NNN_description.sql` (next sequential number).
2. All statements must be idempotent (`IF NOT EXISTS`, `ADD COLUMN IF NOT EXISTS`).
3. Never drop columns or rename existing columns.
4. Restart `fazle-core.service` — `start_fpe()` will apply it automatically.
