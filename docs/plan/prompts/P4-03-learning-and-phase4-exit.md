# P4-03 · Learning from Actuals, IFC-SG Alignment and Phase 4 Exit

**Builds on:** P2-08 (outcomes, library governance), P2-05 (productivity library), P3-02 (BIM ingestion), P4-01 (orchestration). **Needs:** an actuals feed from project delivery (purchased quantities, costs, labour hours by activity) and IFC-SG sample models (business track).

Begin by replying with a plan that maps each item under **Done when** to the work that satisfies it. Wait for approval before changing files.

## Read first

- `docs/plan/project-context.md`: guardrail 2, *proposal*.
- Requirements: §6.15 (FR-LRN-04), §6.12 (FR-LAB-05), §6.9 (FR-CRD-06), §13.2 (Phase 4 exit criteria), §14 (estimate variance, labour accuracy, business outcomes).
- `docs/plan/BUILD_LOG.md`.

## Goal

Close the loop from awarded projects back into estimating. Measure estimate accuracy against actual cost and hours, and propose productivity improvements for estimators to approve. Align BIM classification with IFC-SG, and report on Phase 4 exit.

## Scope

FR-LRN-04, FR-LAB-05, FR-CRD-06 (Could), Phase 4 exit criteria.

## Build

1. **Actuals linkage** (FR-LRN-04): import purchased quantities, actual costs and labour hours by activity (file-based first, via the ERP adapter when available). Link them to the frozen tender snapshot's BOQ lines, and produce variance analytics by system, item class and cost component.
2. **Productivity learning** (FR-LAB-05):
   - Statistical updates to baseline productivity from actual hours, e.g. a shrinkage estimate weighted by sample size, with confidence intervals.
   - Present them as proposed library changes through the P2-08 governance queue. Only estimator approval applies them.
3. **IFC-SG alignment** (FR-CRD-06, Could): map canonical fire protection types to IFC-SG classes and property sets where project models use them. Validate sample IFC-SG models, and report unmapped classes.
4. **Exit report** `docs/reports/phase4-exit.md`:
   - bid cycle time against baseline;
   - missed-scope incidents post-award against baseline;
   - estimate variance and labour accuracy on awarded projects with actuals;
   - gap list and recommendation.

## Done when

- A sample actuals import links to snapshot BOQ lines, and variance analytics match hand calculations. Tagged FR-LRN-04.
- Productivity proposals show their statistical basis and have no effect until approved. Tagged FR-LAB-05.
- IFC-SG sample models map to canonical types, with unmapped classes reported (or recorded as deferred if the Could item is descoped). Tagged FR-CRD-06.
- `docs/reports/phase4-exit.md` reports each Phase 4 exit criterion.
- `make req-coverage` shows every FR and NFR covered, or listed as descoped with a decision reference.
- A build log entry is appended.
