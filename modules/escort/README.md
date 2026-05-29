# Escort Module

Vessel escort order processing — inbound client orders → admin drafts → finalized slips.

## Flow

```
Client sends MV / lighter message
  → parse_escort_message()
  → save_escort_programs()   ← dedup guard: skips if active program exists
  → build_admin_message()    ← plain text, no emojis
  → admin_note returned to message_router → sent to admin

Admin fills Escort Name + Escort Mobile, sends completed draft back
  → is_completed_escort_draft() detects it
  → handle_admin_escort_completion()
  → build_final_slip() → sent to original client
  → wbom_escort_programs status → 'confirmed'
```

## Key Functions

| Function | Purpose |
|----------|---------|
| `parse_escort_message(text)` | Extract MV, lighters, importer, cargo from free-form text |
| `save_escort_programs(order, phone, source)` | Insert to `wbom_escort_programs`; dedup-safe |
| `build_admin_draft(mv, lighter)` | Standard plain-text draft for one lighter |
| `build_admin_message(order, phone)` | Full admin message wrapping all lighter drafts |
| `handle_escort_client_message(text, phone, source, is_historical=False)` | Public entry point for client orders |
| `handle_admin_escort_completion(text, phone, source)` | Public entry point for admin completion |
| `is_completed_escort_draft(text)` | Detect admin's filled-in draft |

## Draft Format (Rule 4 — plain text, no emojis)

```
Mother Vessel: MV EXAMPLE
Lighter Vessel: EXAMPLE LIGHTER
Master Mobile: 01XXXXXXXXX
Escort Name:
Escort Mobile:
Date: DD.MM.YYYY (D/N)
Al-Aqsa Security Service
```

## Dedup Logic

`save_escort_programs()` checks `wbom_escort_programs` for a row matching:
- Same MV (case-insensitive, strips "MV " prefix)
- Same lighter vessel (case-insensitive)
- Status in `('draft', 'confirmed', 'Active', 'Assigned')`
- `program_date >= CURRENT_DATE - 3 days`

If found, returns existing `program_id` and skips INSERT.

## Historical Import

Pass `is_historical=True` to `handle_escort_client_message()` when importing
historical data. This saves the DB record but suppresses admin notification.

## DB Table

`wbom_escort_programs` — key columns:
- `program_id`, `mother_vessel`, `lighter_vessel`, `master_mobile`
- `status`: `draft` → `confirmed` → `Active` → `Closed`
- `remarks`: JSON blob with `sender_phone`, `source_bridge`, `escort_name`, `escort_mobile`
- `is_historical`: TRUE for imported records (added in migration 009)
