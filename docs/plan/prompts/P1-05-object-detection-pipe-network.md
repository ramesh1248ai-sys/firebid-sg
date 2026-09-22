# P1-05 · Fire Protection Object Detection and Pipe Network

**Builds on:** P1-03 (geometry, views, scale, grids), P1-04 (confirmed symbol mappings, signatures), P0-05 (metrics, calibration, golden set). **Needs:** golden set for the accuracy report. Synthetic fixtures carry the tests.

Begin by replying with a plan that maps each item under **Done when** to the work that satisfies it. Wait for approval before changing files. This is an XL step: commit per sub-part (symbol instances, pipe network, size association, calibration).

## Read first

- `docs/plan/project-context.md`: *deterministic-first*, *proposal*, *evidence record*, guardrails 1–3.
- Requirements: §6.3 (FR-VIS-03, 06, 09), §6.4 (what QTO needs next), §14 (Phase 1 accuracy KPIs).
- `docs/plan/BUILD_LOG.md`.

## Goal

Detect wet-pipe sprinkler system objects on each sheet, and assemble pipework into a connected network with sizes and classifications. Every detection carries a calibrated confidence and its evidence, ready for takeoff and human verification.

## Scope

FR-VIS-03, FR-VIS-06 (association of annotations to objects), FR-VIS-09.

## Build

1. **Symbol instances:**
   - DXF: block inserts mapped through confirmed mappings.
   - Vector PDF: path clusters matched against legend signatures with rotation and scale tolerance.
   - Record orientation where the symbol encodes it, and the sheet position, view, grid reference and level for each instance.
2. **Pipe candidates:** select fire protection pipework from line geometry using per-consultant configuration (layers, colours, linetypes, lineweights) plus topology (connection to sprinkler and valve instances). Put the configuration in the consultant profile next to the symbol mappings.
3. **Network graph** (Shapely + networkx):
   - Merge collinear segments.
   - Snap endpoints within a tolerance derived from scale.
   - Nodes are junctions, sprinklers, valves and ends; edges are runs.
   - Keep each edge's geometry reference for evidence.
4. **Run classification:** main, branch, riser or drop.
   - Risers come from riser symbols and schematics.
   - Drops at sprinklers are marked "vertical, not drawn", for rule-derivation in P1-07.
5. **Size association** (FR-VIS-06):
   - Parse size annotations (`150Ø`, `DN150`, `150mm`, `6"`, `Ø65`) to nominal DN.
   - Attach each annotation to the nearest parallel run within a distance and angle tolerance, with a confidence.
   - Propagate size along a run until a reducer, tee or size change.
   - When two annotations disagree on one run, flag a conflict rather than choosing one.
6. **Vision assist (optional, bounded):**
   - Use it only for ambiguous clusters the deterministic matcher cannot resolve: send the crop to the gateway for classification.
   - Record the method as `vision`.
   - A vision-only detection enters with a capped confidence, which puts it at the top of the review queue.
7. **Confidence and calibration** (FR-VIS-09):
   - Compute confidence from features: match residual, annotation distance, topology consistency, method.
   - Fit isotonic calibration on golden and synthetic data with `firebid-eval`.
   - Store calibrated confidence; the reliability curve appears in the eval report.
8. **Output:** DetectedObject records as proposals with evidence references (geometry IDs, method, view). Nothing is auto-accepted.
9. **Eval predictor:** implement the predictor for the Phase 1 detection suite (sprinkler counts by type, pipe length by DN, valves by type, missed and false detections).

## Done when

- Synthetic fixtures produce exact sprinkler counts, pipe lengths within 0.5% per DN, and correct valve counts. Tagged FR-VIS-03.
- Size propagation tests pass for a run with a reducer, and for a run with conflicting annotations (flagged, not guessed). Tagged FR-VIS-06.
- Every DetectedObject has method, evidence reference, view, grid reference and calibrated confidence; a completeness check enforces it. Tagged FR-VIS-09.
- The calibration test shows expected calibration error within the configured tolerance on the synthetic hold-out set. Tagged FR-VIS-09.
- `firebid-eval run --suite p1_detection` produces the report on the golden set when present. Record actuals against the Phase 1 targets (sprinkler count ≥ 98%, pipe length within ±5%) in the build log, with a gap analysis for any metric below target.
- A build log entry is appended.
