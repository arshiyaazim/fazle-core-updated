# Workspace P0 Execution Backlog — April 29, 2026

This backlog turns the `P0` section of
[WORKSPACE_IMPLEMENTATION_PLAN_2026-04-29.md](WORKSPACE_IMPLEMENTATION_PLAN_2026-04-29.md)
into a concrete execution sequence.

The goal is to start with the highest-value trust and correctness gaps,
while keeping the batch model consistent with the rest of the Fazle Core
docs.

## Execution Rules

- run these batches in a versioned copy, not directly in the live folder
- keep each batch independently testable and rollback-safe
- prefer schema additions over destructive schema changes
- preserve auditability for every operator-visible correction path
- do not increase worker count during this sequence

## Batch Sequence

## B26 — Reviewed Reply Memory

Technical design:
[B26_REVIEWED_REPLY_MEMORY_DESIGN.md](B26_REVIEWED_REPLY_MEMORY_DESIGN.md)

### Problem

Admin `EDIT` currently fixes one draft only. The system does not reuse
that correction on later same-intent messages.

### Goal

Promote reviewed draft edits into reusable supervised reply guidance.

### Main deliverables

- reviewed-reply persistence model
- intent and role normalization for reuse matching
- write path from admin `EDIT` into reviewed memory
- read path during future draft generation
- audit fields showing why a reviewed reply was chosen
- operator-facing inspection surface for reviewed patterns

### Suggested backend slices

- reply-review store and retrieval module
- admin command integration for reviewed save decisions
- draft generation hook before final fallback generation
- observability counters for reviewed-hit and reviewed-miss

### Suggested dashboard slices

- reviewed pattern list or detail view
- draft badge showing reviewed influence
- drill-through from draft to reviewed source entry

### Risks

- overfitting one edited reply to too broad an intent bucket
- poor normalization causing wrong reuse across roles
- hidden behavior if reviewed influence is not transparent to operators

### Exit criteria

- repeated same-intent corrections measurably decrease
- reviewed origin is visible in operator workflow
- turning off reviewed reuse is possible by config or guarded switch

### Minimum validation

- unit tests for reviewed lookup specificity
- unit tests for edit-to-reviewed persistence path
- regression tests showing drafts still generate when no reviewed match exists

## B27 — Inbound Media Normalization

### Problem

The runtime has OCR and transcription capability, but generic inbound
media still does not enter the main workflow as structured evidence.

### Goal

Make inbound media a first-class input to the existing business engine.

### Main deliverables

- normalized media envelope for image, audio, document, and video
- OCR integration for supported image paths
- transcription integration for supported audio paths
- persisted media status and extracted text linked to the message record
- safe failure and retry handling for media processing

### Suggested backend slices

- media-normalization helper in the inbound path
- extraction dispatch layer for OCR versus transcription
- persistence for extraction status and extracted text
- failure classification for timeout, unsupported type, and extraction error

### Suggested dashboard slices

- message-level media status in conversations
- extraction preview in investigations
- failed-media visibility for operators

### Risks

- high-latency media processing delaying normal message handling
- unsupported file types creating confusing operator states
- duplicate media submissions reprocessing unnecessarily

### Exit criteria

- inbound media produces structured evidence instead of placeholder-only text
- conversations show extraction outcome and fallback clearly
- failures are inspectable without shell access

### Minimum validation

- sample image and voice-note integration tests
- timeout and fallback tests
- duplicate-media handling tests

## B28 — Payment Correction And Reversal

### Problem

The payment forward path is operational, but correction and reversal
workflow is too thin for safe day-to-day finance handling.

### Goal

Support adjustment and reversal through audited operator workflow.

### Main deliverables

- explicit reversal and adjustment action model
- immutable ledger-preserving correction records
- admin command or protected UI workflow for correction
- corrected-state visibility in drafts, transactions, and reports
- reconciliation-aware handling of corrected payments

### Suggested backend slices

- correction and reversal service module
- admin action integration with strict validation rules
- ledger and draft status extensions for corrected flows
- audit entries for every correction decision

### Suggested dashboard slices

- corrected payment badges and timelines
- transaction detail showing original and correction linkage
- operator filters for corrected versus normal payments

### Risks

- ambiguous rules for partial payment versus full reversal
- accidental double-correction if idempotency is weak
- operator confusion if corrected totals are not rendered clearly

### Exit criteria

- operators can correct wrong payments without direct database work
- every correction leaves a traceable audit path
- reporting distinguishes original, adjusted, and reversed outcomes

### Minimum validation

- unit tests for correction rule validation
- integration tests for original plus correction transaction chains
- regression tests for standard `PAID` flow remaining unchanged

## Recommended Order Inside P0

1. `B26` first because it improves daily reply quality immediately and
   creates value from existing operator work.
2. `B27` second because media is already partially implemented and will
   improve both workflow quality and investigations.
3. `B28` third because finance correction needs more explicit rules and
   should build on the stronger evidence model from the earlier batches.

## Handoff To P1

Start `P1` only after:

- reviewed reuse behavior is stable and inspectable
- media evidence is present in normal investigations
- payment correction is auditable and operator-safe

At that point the next natural sequence is:

- vessel and lighter lifecycle hardening
- attendance operations workflow
- investigation and reconciliation surfaces