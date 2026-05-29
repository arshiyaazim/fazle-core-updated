# Dashboard URL Spec

This document defines the preferred frontend direction for Fazle Core:

- one webapp, served by Fazle Core itself
- one origin: `/dashboard`
- JSON data loaded from existing authenticated endpoints
- no separate `/webapp` deployment in the current plan

The goal is to turn the current tabbed admin shell into a page-addressable
dashboard without splitting the UI across multiple runtimes.

Implementation order is defined in
[DASHBOARD_IMPLEMENTATION_BACKLOG.md](DASHBOARD_IMPLEMENTATION_BACKLOG.md).

## Decision

Chosen direction:

- Keep Fazle Core as the backend and the frontend host.
- Keep nginx in front of Fazle Core.
- Treat `/dashboard` as the only browser application root.
- Expand the current dashboard shell into page-addressable routes.

Not chosen right now:

- `/webapp`
- `:3000` standalone React runtime
- `panel.html`
- a second frontend deployed outside Fazle Core

## Route model

Near-term implementation model:

- `GET /dashboard` serves the shell.
- `GET /dashboard/<page>` should also serve the same shell.
- client-side navigation chooses the active page.
- page data loads from authenticated JSON endpoints using `X-Internal-Key`.

This keeps the deployment simple while still allowing direct links to
specific pages.

## Page map

### 1. `/dashboard`

- Role: entry URL
- Behavior: redirect in-app to `/dashboard/overview`
- Auth UX: show login overlay if no valid API key is stored
- Current source: current shell in `app/static/dashboard.html`
- Status: exists now as the shell root

### 2. `/dashboard/overview`

- Role: first page after login
- Purpose: overall system state and operator summary
- Must show:
  - safe mode / live mode
  - bridge status
  - top counts
  - scheduler summary
  - backup freshness
  - audit summary
- Current APIs:
  - `/admin/overview`
- Status: backed by current dashboard data model

### 3. `/dashboard/drafts`

- Role: operator review queue
- Purpose: manage pending outbound work in one place
- Must show:
  - normal reply drafts
  - payment drafts
  - future gap-action drafts in the same page, not a separate app
- Current APIs:
  - `/admin/drafts`
  - `/admin/payment-drafts`
- Future API addition:
  - optional server-side filter support for `draft_type`
- Status: page should be created from the existing Drafts tab

### 4. `/dashboard/conversations/:phone`

- Role: phone-centric investigation page
- Purpose: inspect one contact across messages, identity, and admin actions
- Must show:
  - last messages
  - last contact time
  - canonical phone and last-10 variants
  - role / contact classification
  - open draft history for this phone
  - gap status for this phone
- Current APIs to reuse or extend:
  - admin NL chat history query path
  - admin NL last-contact query path
  - existing message tables behind new HTTP wrappers
- Status: future page, high value

### 5. `/dashboard/gaps`

- Role: operational monitoring page
- Purpose: show gap alerts and action-required items together
- Must show:
  - current gap alerts
  - severity, duration, cause
  - linked gap-action draft id when one exists
  - daily report summary
  - recent system-delay events
- Current sources:
  - `modules/gap_detector`
  - `reports/daily_gap_report.txt`
  - `fazle_draft_replies` with `draft_type='gap_action'`
- Future API additions:
  - `/api/gaps`
  - `/api/gaps/{phone}` or equivalent filter
- Status: future page, not yet exposed in HTTP UI

### 6. `/dashboard/escort`

- Role: escort operations workspace
- Purpose: follow escort order to release to payment draft
- Must show:
  - active escort programs
  - release-ready items
  - extraction history
  - escort payment drafts
- Current APIs:
  - `/escort-slip/extractions`
  - `/payment/escort-draft`
  - escort lifecycle flows behind existing modules
- Status: future page, backend mostly exists

### 7. `/dashboard/payroll`

- Role: payroll operations workspace
- Purpose: view runs and transition payroll states
- Must show:
  - payroll runs list
  - run detail
  - transitions
  - paid / cancel actions
- Current APIs:
  - `/payroll/runs`
  - `/payroll/runs/{run_id}`
  - `/payroll/compute`
  - `/payroll/run/{run_id}/transition`
- Status: future page, backend exists now

### 8. `/dashboard/recruitment`

- Role: recruitment funnel workspace
- Purpose: review leads and candidate progress
- Must show:
  - recent sessions
  - score and funnel stage
  - candidate contact details
- Current APIs:
  - `/admin/recruitment`
- Status: future page, backend exists now

### 9. `/dashboard/reports`

- Role: reporting workspace
- Purpose: run and inspect generated reports
- Must show:
  - available reports
  - argument form
  - JSON preview
  - file download links when relevant
- Current APIs:
  - `/reports`
  - `/reports/{name}`
- Status: already represented by current Reports tab

### 10. `/dashboard/scheduler`

- Role: scheduled jobs workspace
- Purpose: inspect and manually trigger jobs
- Must show:
  - job status
  - next run
  - last status
  - run count
  - manual trigger actions for allowed jobs
- Current APIs:
  - `/scheduler/status`
  - `/scheduler/run/{job_name}`
- Status: already represented by current Scheduler tab

### 11. `/dashboard/backups`

- Role: backup operations page
- Purpose: inspect backup freshness and run a backup manually
- Must show:
  - backup list
  - freshness state
  - manual backup button
- Current APIs:
  - `/backup/status`
  - `/backup/list`
  - `/backup/run`
  - `/backup/rotate`
- Status: already represented by current Backups tab

### 12. `/dashboard/rag`

- Role: knowledge-base workspace
- Purpose: inspect index health, search, and rebuild the index
- Must show:
  - document counts
  - source breakdown
  - search results
  - rebuild control
- Current APIs:
  - `/rag/stats`
  - `/rag/search`
  - `/rag/answer`
  - `/rag/reindex`
- Status: already represented by current RAG tab

### 13. `/dashboard/observability`

- Role: system diagnostics page
- Purpose: inspect HTTP metrics and recent errors
- Must show:
  - request summary
  - status code totals
  - route latency summary
  - recent error groups
  - link to `/metrics`
- Current APIs:
  - `/observability/summary`
  - `/observability/errors`
  - `/metrics`
- Status: already represented by current Observability tab

### 14. `/dashboard/users`

- Role: RBAC management page
- Purpose: manage admin users and API keys
- Must show:
  - user list
  - roles
  - disable action
  - issue key action
  - add user form
- Current APIs:
  - `/admin/users`
  - `/admin/users/{phone}/role`
  - `/admin/users/{phone}/disable`
  - `/admin/users/{phone}/apikey`
- Status: already represented by current Users tab

### 15. `/dashboard/audit`

- Role: audit investigation page
- Purpose: inspect admin activity and denied actions
- Must show:
  - command history
  - actor
  - result
  - denial reason
  - filter by command
- Current APIs:
  - `/admin/audit`
- Status: already represented by current Audit tab

### 16. `/dashboard/settings`

- Role: configuration page
- Purpose: expose safe operational settings without opening the full server config
- Must show:
  - safe mode status
  - bridge settings summary
  - scheduler enabled state
  - environment-sensitive values only in redacted form
- Current APIs to reuse:
  - `/admin/safe-mode`
  - `/health/deep`
  - scheduler status
- Status: future page

## Data and auth rules

- Every dashboard page uses authenticated JSON endpoints.
- `X-Internal-Key` remains the current browser auth mechanism.
- `/dashboard/*` routes must not embed sensitive data into server-rendered HTML.
- `/metrics` remains unauthenticated on loopback only.
- Webhooks and send APIs stay outside the dashboard route space.

## Retirement plan for old frontend direction

Treat these as retired or non-goals for the current UI strategy:

- `/webapp`
- `http://127.0.0.1:3000`
- `panel.html`
- the standalone React multi-panel app as the primary admin UI

That code can stay archived for reference, but it should not define the
future URL map for Fazle Core.