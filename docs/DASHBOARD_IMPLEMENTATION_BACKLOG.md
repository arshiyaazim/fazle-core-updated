# Dashboard Implementation Backlog

This backlog translates [DASHBOARD_URL_SPEC.md](DASHBOARD_URL_SPEC.md)
into a build order.

Ordering principle:

- build the highest-value pages first
- prefer pages already backed by live APIs
- postpone pages that need new HTTP wrappers or cross-table joins
- keep one frontend direction only: `/dashboard/*`

## Priority model

- `P0` = platform work required before page routing is real
- `P1` = highest-value pages with strong backend support now
- `P2` = important operator pages with mostly-ready backend support
- `P3` = pages that need new API wrappers or more integration work
- `P4` = polish and lower-urgency pages

## P0 — Dashboard foundation

### 1. Shell routing for `/dashboard/*`

- Goal: serve the same shell for `/dashboard` and page-addressable routes
- Why first: every other page depends on direct-link support
- Backend work:
  - make `GET /dashboard/{page}` return `dashboard.html`
  - optionally support nested routes such as `/dashboard/conversations/{phone}`
- Frontend work:
  - read `window.location.pathname`
  - map path to active page/tab
  - update browser history during in-app navigation
- Done when:
  - `/dashboard/overview` opens the correct page directly
  - refresh on a dashboard subpage does not 404

### 2. Shared page layout and nav state

Status: done

- Goal: convert the current tabbed shell into a page-aware layout
- Why first: avoids rebuilding nav logic per page
- Frontend work:
  - define a page registry keyed by route slug
  - centralize page title, subtitle, active nav state, and page loader
  - preserve existing login overlay and refresh behavior
- Done when:
  - one route map drives navigation for all existing and future pages
  - current shell exposes page title, subtitle, and route state above the page body

### 3. Shared data/error/loading patterns

Status: done

- Goal: standardize how pages fetch protected JSON
- Why first: current tab code duplicates fetch/error handling
- Frontend work:
  - shared request wrapper for `X-Internal-Key`
  - shared loading, empty, and error states
  - consistent retry/refresh controls
- Done when:
  - page modules stop duplicating the same fetch boilerplate
  - box-level loading and error states render consistently during route changes

## P1 — First delivery pages

### 4. `/dashboard/overview`

Status: done

- Why here: highest operational value, already backed by `/admin/overview`
- Current APIs:
  - `/admin/overview`
- Scope:
  - mode banner
  - counts
  - bridges
  - scheduler mini
  - backup mini
  - audit summary
- Backend gap: none required for first pass
- Done when:
  - current Overview tab becomes a stable route page
  - direct load and refresh on `/dashboard/overview` keep the correct page metadata

### 5. `/dashboard/drafts`

Status: done

- Why here: core operator workflow, already has live data
- Current APIs:
  - `/admin/drafts`
  - `/admin/payment-drafts`
- Scope:
  - reply drafts
  - payment drafts
  - clear visual distinction by draft type/status
- Backend gap:
  - optional later filter by `draft_type`
- Done when:
  - operators can review the active outbound queues from one route
  - direct load and refresh on `/dashboard/drafts` keep the correct page metadata

### 6. `/dashboard/audit`

Status: done

- Why here: high-value for trust and admin investigations
- Current APIs:
  - `/admin/audit`
- Scope:
  - command filter
  - actor label / phone
  - allow/deny result
  - denial reason
- Backend gap: none required for first pass
- Done when:
  - current Audit tab becomes a usable page with direct-link support

### 7. `/dashboard/users`

Status: done

- Why here: already implemented in backend and part of daily admin control
- Current APIs:
  - `/admin/users`
  - `/admin/users/{phone}/disable`
  - `/admin/users/{phone}/apikey`
  - `/admin/users/{phone}/role`
- Scope:
  - user list
  - add user
  - issue key
  - disable user
- Backend gap:
  - none for current capability set
- Done when:
  - RBAC management no longer depends on the tab shell only

## P2 — Existing pages worth stabilizing next

### 8. `/dashboard/scheduler`

Status: done

- Why here: useful for operations and already backed by live APIs
- Current APIs:
  - `/scheduler/status`
  - `/scheduler/run/{job_name}`
- Scope:
  - job list
  - last/next run
  - manual trigger for allowed jobs
- Backend gap:
  - optional allowlist metadata for which jobs may be triggered from UI
- Done when:
  - operators can inspect and run jobs from the page safely
  - page-level summary, action bar, and direct route load are stable

### 9. `/dashboard/backups`

Status: done

- Why here: operationally important and almost fully ready
- Current APIs:
  - `/backup/status`
  - `/backup/list`
  - `/backup/run`
  - `/backup/rotate`
- Scope:
  - freshness
  - history list
  - manual run
- Backend gap:
  - none for first pass
- Done when:
  - backup status is visible without using ad hoc shell commands
  - page-level summary, action bar, and direct route load are stable

### 10. `/dashboard/reports`

Status: done

- Why here: current tab already works and is easy to route-cleanly
- Current APIs:
  - `/reports`
  - `/reports/{name}`
- Scope:
  - report picker
  - args form
  - JSON preview
  - file/download affordances where relevant
- Backend gap:
  - optional report metadata for nicer forms later
- Done when:
  - report execution is page-based and linkable
  - page-level summary, action bar, and direct route load are stable

### 11. `/dashboard/observability`

Status: done

- Why here: strong operational value and live APIs already exist
- Current APIs:
  - `/observability/summary`
  - `/observability/errors`
  - `/metrics`
- Scope:
  - request summary
  - route summaries
  - recent errors
  - metrics link-out
- Backend gap:
  - none for first pass
- Done when:
  - most common troubleshooting checks are available from one page
  - page-level summary, action bar, and direct route load are stable

### 12. `/dashboard/recruitment`

- Why here: backend exists and page scope is comparatively simple
- Current APIs:
  - `/admin/recruitment`
- Scope:
  - leads list
  - score
  - funnel stage
  - recent activity fields
- Backend gap:
  - optional richer filters later
- Done when:
  - recruitment sessions are visible as a dedicated workspace

### 13. `/dashboard/payroll`

- Why here: important, but slightly more action-heavy than overview/drafts
- Current APIs:
  - `/payroll/runs`
  - `/payroll/runs/{run_id}`
  - `/payroll/compute`
  - `/payroll/run/{run_id}/transition`
- Scope:
  - runs list
  - run detail
  - compute/transition actions
- Backend gap:
  - optional HTTP wrapper for paid/cancel flows if they should move out of chat-only admin commands
- Done when:
  - payroll run inspection and core transitions are page-driven

## P3 — New visibility pages requiring backend additions

### 14. `/dashboard/gaps`

Status: first pass done

- Why here: operationally valuable, but needs new API shaping
- Current sources:
  - `modules/gap_detector`
  - `reports/daily_gap_report.txt`
  - `fazle_draft_replies` with `draft_type='gap_action'`
- Needed backend work:
  - first pass now served by `/admin/gaps`
  - add per-phone or filtered gap query support later
  - enrich historical gap parsing beyond current alert-log extraction
- Frontend scope:
  - alert list
  - severity and cause
  - action-required state
  - daily summary view
- Done when:
  - operators can inspect monitoring and action state without parsing WhatsApp alerts or text files

### 15. `/dashboard/conversations/:phone`

- Why here: very high value, but requires the most new aggregation work
- Current sources:
  - admin NL chat history logic
  - admin NL last-contact logic
  - message and identity tables
- Needed backend work:
  - add phone-focused HTTP endpoint(s)
  - expose canonical phone, aliases, role, recent messages, open drafts, and gap state in one response
- Frontend scope:
  - message timeline
  - identity panel
  - draft panel
  - last-contact panel
- Done when:
  - a phone number becomes a first-class investigation page

### 16. `/dashboard/escort`

- Why here: backend is substantial, but the UI needs several data joins
- Current APIs and sources:
  - `/escort-slip/extractions`
  - escort lifecycle modules
  - escort payment draft flow
- Needed backend work:
  - likely dedicated escort workspace endpoints
  - active-program and release-ready summaries
- Frontend scope:
  - active programs
  - release state
  - extraction history
  - draft creation outcomes
- Done when:
  - escort operations can be followed end-to-end in one page

## P4 — Lower urgency or polishing work

### 17. `/dashboard/rag`

Status: done

- Why later: already exposed by a working tab and lower priority than operator control pages
- Current APIs:
  - `/rag/stats`
  - `/rag/search`
  - `/rag/answer`
  - `/rag/reindex`
- Scope:
  - route-cleanup and better page polish, not major backend work
- Done when:
  - RAG is fully page-addressable and consistent with the rest of the dashboard

### 18. `/dashboard/settings`

- Why later: useful, but easy to overbuild too early
- Current sources:
  - `/admin/safe-mode`
  - scheduler and health summaries
- Needed backend work:
  - dedicated redacted settings endpoint
  - explicit editable vs read-only config boundary
- Done when:
  - operators can see safe runtime settings without exposing secrets

## Recommended implementation sequence

If you want the smallest practical delivery slices, build in this order:

1. P0 foundation
2. `/dashboard/overview`
3. `/dashboard/drafts`
4. `/dashboard/audit`
5. `/dashboard/users`
6. `/dashboard/scheduler`
7. `/dashboard/backups`
8. `/dashboard/reports`
9. `/dashboard/observability`
10. `/dashboard/recruitment`
11. `/dashboard/payroll`
12. `/dashboard/gaps`
13. `/dashboard/conversations/:phone`
14. `/dashboard/escort`
15. `/dashboard/rag`
16. `/dashboard/settings`

## What not to build during this backlog

- do not revive `/webapp` as a parallel admin frontend
- do not introduce `panel.html`
- do not split pages across port `3000` and port `8200`
- do not build a second auth model before the current dashboard route model is stable