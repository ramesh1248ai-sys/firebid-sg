# P1-08 · Verification Workbench

**Builds on:** P1-07 (QTO items, duplicates, manual items API), P1-03/P1-04 (calibration and mapping APIs), P1-01 (tile viewer). **Needs:** 2–3 estimators for a usability session before the step closes.

Begin by replying with a plan that maps each item under **Done when** to the work that satisfies it. Wait for approval before changing files. This is an XL step: commit per sub-part (viewer and overlay, queue and actions, manual tools, duplicates and gate, performance).

## Read first

- `docs/plan/project-context.md`: guardrail 2 (only people verify), *gate*.
- Requirements: §6.5 (FR-REV-01 to 04, 06), FR-QTO-11 (UI), NFR-10, NFR-12, §5 (G1).
- `docs/plan/BUILD_LOG.md`.

## Goal

Build the screen where estimators spend their day. They see every AI proposal on the drawing, work a risk-ranked queue, and accept, edit or reject quickly in bulk. They add what was missed and resolve duplicates. When coverage and integrity rules are met, the Senior Estimator passes G1.

## Scope

FR-REV-01, 02, 03, 04, 06, FR-QTO-11 (UI), NFR-10, NFR-12.

## Build

1. **Viewer and overlay** (FR-REV-01):
   - OpenSeadragon deep-zoom tiles with a synchronised **Canvas/WebGL overlay** in sheet-mm coordinates (ADR-005).
   - Hit-testing and lasso selection use a client-side spatial index (e.g. Flatbush).
   - Only selected and hovered items are drawn as SVG, for crisp outlines and accessibility.
   - Layers by object type, status (proposed, verified, edited, rejected, manual) and confidence band.
   - Redraw only the viewport's objects each frame, using the spatial index, so tens of thousands of objects stay interactive.
   - Queue, register and item lists use TanStack Table with TanStack Virtual, so long lists render only visible rows.
   - Offer a pop-out viewer window synchronised with the queue, for dual monitors (NFR-12).
2. **Click-through:** from any QTO line or queue item, zoom to its evidence; clicking an object opens its QTO item panel with the full evidence record. The trip from a line to its evidence takes at most two clicks (NFR-10).
3. **Review queue** (FR-REV-02):
   - Order by (1 − calibrated confidence) × impact.
   - Impact = quantity × rate where a rate exists (P1-10); otherwise a configurable weight per item class.
   - Filters: sheet, system, level, item type, status.
4. **Actions** (FR-REV-03):
   - Accept, edit (attributes, quantity via re-measure) or reject, for one item or in bulk (lasso, by type in view, or a queue page).
   - Reason codes are required on edit and reject.
   - Keyboard shortcuts and undo.
   - Every action goes through the domain state machine and writes audit events.
5. **Manual takeoff tools** (FR-QTO-11 UI): a count tool and a polyline length tool. Both respect verified scale and are disabled with an explanation otherwise. Attributes are picked from the canonical library.
6. **Duplicates, mappings and scale:** side-by-side evidence for DuplicateGroups with keep and merge decisions. Legend mapping confirmation and scale calibration panels reuse the P1-03 and P1-04 APIs.
7. **Coverage and G1** (FR-REV-04):
   - A dashboard of percentage verified by item count and by value.
   - A G1 action for the Senior Estimator, enabled only when:
     - coverage meets the policy (100% in Phase 1);
     - no DuplicateGroup is unresolved;
     - no unmapped symbol type is in scope;
     - the evidence completeness check passes.
   - A disabled G1 lists the blocking items with links.
8. **Correction capture** (FR-REV-06): every edit or rejection emits a labelled correction event (before, after, reason, detector method and version) into a dataset table the eval harness can read, subject to the data policy in requirements §11.4.
9. **Performance:** measure pan and zoom frame rate, hit-test latency and action latency on a large-format sheet with 5,000 overlay objects (the expected case) and 20,000 (headroom).

## Done when

- The Playwright E2E passes on a synthetic bid: open the queue → bulk-accept a page → edit one item with a reason → reject one with a reason → add a manual item → resolve a duplicate → coverage reaches 100% → the Senior Estimator passes G1 → the audit log shows each action. Tagged FR-REV-01, 03, 04 and FR-QTO-11.
- Negative E2E tests show G1 disabled, with its blocking list, for each condition in item 7. Tagged FR-REV-04.
- A test shows the evidence for any BOQ-feeding QTO line is reached in two clicks or fewer. Tagged NFR-10 and FR-REV-01.
- Queue ordering follows the formula on a crafted dataset. Tagged FR-REV-02.
- Correction events are written with before, after, reason and detector version. Tagged FR-REV-06.
- Performance is measured and recorded in the build log. Tagged NFR-12. Targets:
  - with 5,000 objects: pan and zoom at 60 fps on the reference workstation, and action feedback under 200 ms;
  - with 20,000 objects: at least 30 fps, and hit-testing under 16 ms.
- Findings from the estimator usability session, and the changes made, are recorded in the build log.
- A build log entry is appended.
