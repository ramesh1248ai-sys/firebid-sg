# P2-02 · Revision Deltas, Multi-Bid Projects and Sampling Mode

**Builds on:** P1-02 (revisions, addenda), P1-07 (deterministic QTO recompute), P1-08 (workbench), P0-03 (per-bid scoping). **Needs:** Phase 1 accuracy evidence per item category, to configure sampling.

Begin by replying with a plan that maps each item under **Done when** to the work that satisfies it. Wait for approval before changing files.

## Read first

- `docs/plan/project-context.md`: guardrails 2 and 8, immutability.
- Requirements: §6.2 (FR-DOC-08), §6.4 (FR-QTO-12), §6.1 (FR-BID-04), §6.5 (FR-REV-05), §5 (addenda loop).
- `docs/plan/BUILD_LOG.md`, `docs/reports/phase1-exit.md`.

## Goal

When an addendum lands, estimators see exactly what changed and re-verify only that. A project bid to several main contractors shares one verified takeoff while each bid keeps its own documents and prices. Proven item categories move from full review to statistically sound sampling.

## Scope

FR-DOC-08, FR-QTO-12, FR-BID-04, FR-REV-05.

## Build

1. **Revision comparison** (FR-DOC-08): align two revisions of a sheet (grid system, then title block frame, then a geometric fit). Diff the fire protection elements as added, removed or changed, and show the diff as a workbench overlay with a change list.
2. **Delta QTO** (FR-QTO-12):
   - Re-process only the affected sheets.
   - Match elements across revisions by geometry and attributes.
   - Unchanged elements keep their verification. Changed elements return to Proposed with the previous value shown. Removed elements are superseded.
   - Produce a delta report (quantity changes per BOQ line against the baseline) and reopen affected stages and gates for those items only.
3. **Addendum propagation:** extend the P1-02 affected-items query to QTO items, BOQ lines and clarification candidates.
4. **Multi-bid projects** (FR-BID-04):
   - A project can hold several bids.
   - A verified QTO baseline is shared copy-on-write: a bid-specific edit creates a bid-scoped version.
   - Client documents, client BOQs, commercial terms and prices are scoped to their bid.
   - A user of one bid sees another bid's client-specific data only if they belong to both.
5. **Sampling mode** (FR-REV-05):
   - Per item category, a configurable acceptance-sampling plan (sample size from lot size and acceptable quality level).
   - The sample is drawn randomly and recorded.
   - If the sample error exceeds the threshold, the whole category returns to full review.
   - Coverage policy per category is configured by the Senior Estimator and audited.
   - G1 coverage rules respect the configured policy.

## Done when

- Diffing two synthetic revisions reports exactly the seeded additions, removals and changes. Tagged FR-DOC-08.
- After delta QTO, unchanged verified items keep Verified, changed items are Proposed with their previous value, and the delta report matches the seeded changes. Tagged FR-QTO-12.
- A two-bid project test: a shared baseline is visible to both bids; a bid-specific edit does not change the other bid; a member of only one bid cannot read the other bid's client BOQ or prices. Tagged FR-BID-04.
- A sampling test: a category with seeded errors above threshold escalates to full review; one within threshold passes on the sample; the sample selection is recorded. Tagged FR-REV-05.
- A build log entry is appended.
