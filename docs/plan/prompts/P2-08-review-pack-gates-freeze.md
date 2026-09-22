# P2-08 · Review Pack, Gates G2–G4, Submission Freeze and Outcomes

**Builds on:** P2-04, P2-05 (estimate), P2-07 (risks, qualifications, G3 readiness), P0-03 (permission matrix), P0-02 (versioning, audit, storage). **Needs:** decision D5 (named approvers) for production role assignment.

Begin by replying with a plan that maps each item under **Done when** to the work that satisfies it. Wait for approval before changing files.

## Read first

- `docs/plan/project-context.md`: guardrails 2, 7 and 8, *gate*, immutability.
- Requirements: §6.14 (FR-PKG-01, 02, 03), §6.15 (FR-LRN-02, 03), §5 (G2–G4), §7 (bid lifecycle), §3 (RACI).
- `docs/plan/BUILD_LOG.md`.

## Goal

Approvers get a clear review pack and sign gates G2, G3 and G4 in the platform. The submitted bid is frozen immutably with everything behind it. Outcomes and library updates flow back under estimator control.

## Scope

FR-PKG-01, FR-PKG-02, FR-PKG-03, FR-LRN-02, FR-LRN-03.

## Build

1. **Review pack** (FR-PKG-01): generated as PDF and xlsx, containing:
   - estimate summary by component and system;
   - key cost drivers and margin;
   - risk allowances with treatments;
   - top quantity variances against the client BOQ;
   - open clarifications and issues;
   - unpriced lines;
   - G1 coverage summary.

   Each section links back into the platform.
2. **Gates** (FR-PKG-02):
   - G2 (Senior Estimator), G3 and G4 (Commercial Director) per the permission matrix.
   - Each gate runs its readiness checks (trace check, price provenance, G3 readiness) and records approver, time, comments and the snapshot hash it approved.
   - The platform offers downloads of submission files after G4. It has no path that transmits a bid to a client automatically.
3. **Submission freeze** (FR-PKG-03):
   - On G4, write an immutable snapshot: a manifest of every underlying record version (QTO, evidence, BOQ, prices, sources, clarifications, qualifications, approvals) with content hashes, plus exported files.
   - Store it in the object-lock bucket.
   - The bid moves to Submitted, and a `verify_snapshot()` function checks integrity.
4. **Outcome capture** (FR-LRN-02): record awarded or lost, awarded price if known, reasons and competitor feedback. The bid lifecycle moves to Awarded, Lost or Withdrawn.
5. **Library governance** (FR-LRN-03): proposed updates to the rate and productivity libraries (from new quotations, outcomes or estimator suggestions) enter a review queue. Only an estimator approval applies them, and each applied change is versioned and audited.

## Done when

- The review pack for a synthetic bid contains every listed section, with figures reconciling to the estimate. Tagged FR-PKG-01.
- Gate tests: each gate is approvable only by its role; readiness failures block approval with reasons; approvals record the snapshot hash. A test confirms no automatic external transmission path exists. Tagged FR-PKG-02.
- After G4 the snapshot verifies; a tampered snapshot object fails `verify_snapshot()`; writes to snapshot records are rejected. Tagged FR-PKG-03.
- Outcome capture updates the lifecycle and is reported. Tagged FR-LRN-02.
- A proposed library change has no effect until estimator approval, and applies as a new version afterwards. Tagged FR-LRN-03.
- A build log entry is appended.
