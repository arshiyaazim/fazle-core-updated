# Workspace Implementation Plan — April 29, 2026

This plan turns the findings in
[WORKSPACE_REVIEW_2026-04-29.md](WORKSPACE_REVIEW_2026-04-29.md)
into a prioritized delivery sequence.

Concrete next-step backlog:
[WORKSPACE_P0_EXECUTION_BACKLOG_2026-04-29.md](WORKSPACE_P0_EXECUTION_BACKLOG_2026-04-29.md)

Planning principles:

- fix the highest-value operational gaps first
- prefer changes that improve correctness before adding new surface area
- preserve Fazle Core as the single primary runtime engine
- avoid worker scaling changes until single-process assumptions are removed
- ship in small batches that can be tested and rolled back cleanly

## Priority Model

- `P0` = correctness or control gaps that affect day-to-day operator trust
- `P1` = major workflow upgrades that unlock clear operational value next
- `P2` = important operator visibility and reconciliation improvements
- `P3` = scale and platform upgrades after workflow behavior is safer

## P0 — Correctness And Control First

### 1. Supervised reply memory from admin edits

- Why first: the highest-value gap in the review is that admin edits are
  one-shot corrections and do not improve future replies.
- Goal: promote reviewed draft edits into reusable intent-aware reply
  guidance.
- Scope:
  - define a reviewed-reply store keyed by normalized intent, role, and
    optional business context
  - record when `EDIT` changes a draft in a way that should become a
    reusable example
  - prefer reviewed examples during future draft generation
  - keep auditability so operators know when a reply came from reviewed
    behavior versus fresh generation
- Done when:
  - repeated same-intent messages stop requiring the same manual edit
  - operators can inspect which reviewed patterns are influencing replies

### 2. First-class inbound media routing

- Why first: the workspace already has OCR and transcription capability,
  but generic inbound handling still collapses media into placeholder text.
- Goal: make media messages enter the business workflow as structured
  inputs instead of generic placeholders.
- Scope:
  - define a normalized inbound media envelope for image, audio,
    document, and video
  - route eligible images into OCR extraction
  - route eligible voice notes into transcription
  - persist media metadata and extracted text in a form usable by the
    main router and investigations
  - preserve a safe fallback when media processing fails
- Done when:
  - inbound media can participate in normal workflow decisions without
    manual re-entry by operators
  - conversations and investigations show media-derived text and status

### 3. Payment correction and reversal workflow

- Why first: the forward payment path exists, but mistakes appear harder
  to correct than they should be.
- Goal: make payments reversible and adjustable without database-level
  intervention.
- Scope:
  - add explicit reversal and adjustment commands or equivalent protected
    admin actions
  - preserve immutable audit trail for the original ledger entry and the
    correction entry
  - expose corrected status clearly in admin and reporting surfaces
  - define safe rules for partial correction versus full reversal
- Done when:
  - a wrong payment amount can be corrected through the supported
    operator workflow
  - finance history remains auditable after correction

## P1 — Deepen Core Workflow Quality

### 4. Vessel and lighter identity through the full escort lifecycle

- Why here: escort workflow is already strong, so deepening vessel-aware
  behavior should pay off quickly.
- Goal: treat mother vessel and lighter vessel as first-class operational
  identities, not just extracted fields.
- Scope:
  - normalize vessel and lighter names for matching
  - improve program matching from release evidence back to the correct
    active escort program
  - preserve both identities through attendance, payment, and reports
  - define conflict-handling rules when OCR or text gives ambiguous names
- Done when:
  - release evidence matches programs more reliably
  - reports can distinguish mother-vessel and lighter-vessel activity

### 5. Attendance operator workflow upgrade

- Why here: attendance works, but it still behaves like a sub-workflow
  instead of a full operations area.
- Goal: promote attendance into a clearer operator workflow with better
  review, correction, and visibility.
- Scope:
  - add dedicated attendance review and correction surfaces
  - distinguish manually recorded, supervisor-reported, and escort-
    backfilled attendance
  - support exception handling for absence, mismatch, and duplicate-day
    cases
  - expose clearer attendance summaries in the dashboard
- Done when:
  - operators can review, correct, and understand attendance history
    without relying on ad hoc chat commands alone

### 6. Conversations and investigations enriched with workflow evidence

- Why here: the dashboard already has strong page-first investigation
  routes, so it is the right surface for the next layer.
- Goal: make investigations show the evidence that operators need without
  forcing them back to shell or database tools.
- Scope:
  - display reviewed-reply influence for drafts
  - show media extraction status and source text
  - show payment correction history and reconciliation state
  - show vessel and lighter matching confidence where relevant
- Done when:
  - the main operator investigations can be completed from the dashboard
    without cross-tool jumping

## P2 — Operational Visibility And Reconciliation

### 7. Payment reconciliation visibility

- Why here: reconciliation likely exists in jobs and tables, but the
  operator surface is still thin.
- Goal: make unmatched, corrected, and suspicious payments visible.
- Scope:
  - surface unmatched or partially matched payments in the dashboard
  - show last-10 phone matching outcomes and ambiguity cases
  - add operator actions for confirm, defer, or escalate
- Done when:
  - reconciliation does not happen silently in the background only

### 8. Media operations visibility

- Why here: once media is first-class, it needs operational observability.
- Goal: make OCR and transcription behavior inspectable.
- Scope:
  - processing status, retries, and failures
  - extracted text preview
  - duplicate detection visibility
  - investigation link-outs from relevant conversation threads
- Done when:
  - failed media processing becomes easy to diagnose from operator tools

### 9. Domain-specific reporting improvements

- Why here: escort, attendance, and payment workflows will become more
  useful once their outputs are visible by domain.
- Goal: produce operator-grade reporting around the strongest business
  workflows.
- Scope:
  - vessel and lighter activity summaries
  - attendance exception reports
  - corrected payment and reversal reports
  - reviewed-reply reuse and edit-volume reports
- Done when:
  - owner and admin reviews can rely on built-in reports instead of
    forensic database analysis

## P3 — Scale And Platform Hardening

### 10. Remove single-process coordination assumptions

- Why later: scaling before workflow correctness improves would widen the
  blast radius of current assumptions.
- Goal: prepare the system for higher concurrency safely.
- Scope:
  - move dedup and coordination assumptions out of in-process memory
  - define worker-safe background task behavior
  - verify command idempotency under parallel handling
- Done when:
  - worker count can increase without breaking correctness guarantees

### 11. AI throughput and latency improvements

- Why later: current bottlenecks are operationally acceptable until the
  correctness roadmap above is addressed.
- Goal: reduce queueing and response latency under heavier load.
- Scope:
  - review serialized Ollama access strategy
  - identify safe caching or routing opportunities
  - separate AI-heavy tasks from latency-sensitive operator flows where needed
- Done when:
  - higher traffic does not create disproportionate delay for normal
    operator workflows

## Suggested Delivery Order

### Phase 1

- supervised reply memory from admin edits
- first-class inbound media routing
- payment correction and reversal workflow

### Phase 2

- vessel and lighter identity through the escort lifecycle
- attendance operator workflow upgrade
- conversations and investigations enriched with workflow evidence

### Phase 3

- payment reconciliation visibility
- media operations visibility
- domain-specific reporting improvements

### Phase 4

- remove single-process coordination assumptions
- AI throughput and latency improvements

## Recommended Batch Framing

If this plan is executed in the existing batch style, the most coherent
batch candidates would be:

- `B26` — reviewed reply memory and supervised reuse
- `B27` — inbound media normalization and processing integration
- `B28` — payment correction and reversal workflow
- `B29` — vessel and lighter lifecycle hardening
- `B30` — attendance operations workflow
- `B31` — investigations and reconciliation surfaces
- `B32` — worker-safe coordination and throughput hardening

These labels are suggestions only. They should be reconciled with the
existing future-idea section in
[ROADMAP.md](ROADMAP.md) before implementation starts.

The concrete `P0` execution sequence for `B26` through `B28` is tracked
in
[WORKSPACE_P0_EXECUTION_BACKLOG_2026-04-29.md](WORKSPACE_P0_EXECUTION_BACKLOG_2026-04-29.md).

## Bottom Line

The next wave of work should not be driven by more generic AI features.
It should be driven by operator trust, correction loops, and stronger use
of already-available business signals.

The best near-term return will come from:

1. teaching the system from admin edits
2. making media a real first-class input
3. making payments correctable through supported workflow

After that, vessel-aware lifecycle quality, attendance operations, and
reconciliation visibility should become the next priority tier.