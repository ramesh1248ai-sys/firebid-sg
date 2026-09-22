# P0-05 · Evaluation Harness and Golden Set Tooling

**Builds on:** P0-02 (entities), P0-04 (gateway routes and config, for model comparison). **Needs:** golden set collection by estimators (business track), which uses the template this step creates. Synthetic fixtures let the step finish without it.

Begin by replying with a plan that maps each item under **Done when** to the work that satisfies it. Wait for approval before changing files.

## Read first

- `docs/plan/project-context.md`: leading word *golden set*, guardrail 8 (confidential data).
- Requirements: §13.2 (exit criteria), §14 (KPI definitions), FR-LRN-01, NFR-11, and the acceptance criteria in §6.2–§6.6 that name the golden set.
- `docs/plan/BUILD_LOG.md`.

## Goal

Every later step can measure accuracy the same way, and any route's LLM provider can be changed on evidence rather than opinion. That needs a golden set format that estimators can fill from historical takeoffs, the Phase 1 KPI calculations, a regression gate, and a synthetic fixture generator so CI can test drawing logic without confidential tenders.

## Scope

FR-LRN-01, NFR-11, KPI definitions for Phase 1 (§14).

## Build

1. **Golden set format** (`eval/`):
   - A manifest per tender: ID, consultant, sheets, revisions, input class (vector PDF, DWG, raster).
   - Sheet-level ground truth: counts per canonical object type and attribute, pipe length per nominal diameter, valves by type, known duplicates across sheets, the current revision per sheet number, and client BOQ lines with quantities.

   Data files live in an eval bucket, never in git. Git holds manifests and checksums.
2. **Estimator template** (`eval/templates/golden_takeoff.xlsx`) with one sheet per concept, data validation lists and an instructions tab. Add an importer that validates a filled template and writes normalised JSON, with a readable error report.
3. **Metrics** (`firebid/evals/metrics.py`), using the formulas in requirements §14:
   - sprinkler count accuracy
   - pipe length accuracy by diameter
   - missed-item rate
   - false-detection rate
   - duplicate detection rate
   - drawing number and revision classification accuracy
   - BOQ mapping accuracy
   - confidence calibration (expected calibration error and reliability bins)
4. **Runner CLI:** `firebid-eval run --suite <name> --tenders <ids|all>` calls a predictor interface, which later steps implement. It writes JSON plus a Markdown/HTML report sliced by input class and consultant.
5. **Baselines and regression gate:**
   - `firebid-eval accept --approver <name>` stores the current result as the baseline.
   - `firebid-eval compare` exits non-zero when any metric regresses beyond its configured tolerance (FR-LRN-01).
   - Add a CI job that runs it on the synthetic set, and a manual or nightly job for the private golden set.
6. **Synthetic fixture generator** (`eval/synthetic/`): produces small DXF and vector-PDF fire protection plans from a seed, with known ground truth. Cover at least:
   - a general floor plan with sprinklers, branches and a main with size annotations;
   - the same drawing as DXF;
   - an enlarged plan repeating part of the general plan;
   - a superseded and a current revision of one sheet;
   - an NTS sheet;
   - a legend sheet;
   - a low-resolution raster version.
7. **Dummy predictor** that returns ground truth with controllable noise, to exercise the runner and the regression gate.
8. **Model comparison:** `firebid-eval compare-models --suite <name> --route <route> --models <m1,m2,...>` reruns a suite once per candidate model through temporary route overrides. The candidates come from the models declared in `llm.yaml`, and each must be approved for the route's data class. The report shows quality metrics, cost per tender and latency (p50/p95) side by side, and records the config version and prompt versions used. It is the evidence required before any route's model chain changes (project-context, "Defaults and changes").

## Done when

- Unit tests check every metric against hand-computed examples, including the edge cases zero ground-truth items and all items missed.
- The generator produces every fixture type in item 6, and the ground truth matches the drawn content (a test recounts entities from the generated DXF).
- `firebid-eval run` on the synthetic set with the dummy predictor produces a report with all Phase 1 metrics sliced by input class.
- An injected regression makes `firebid-eval compare` fail in CI, and restoring the predictor makes it pass. Tagged FR-LRN-01.
- A filled sample template imports cleanly, and a template with seeded errors produces a clear error report.
- `compare-models` on the synthetic set with two fake-adapter models of different scripted accuracy produces a side-by-side report that ranks them correctly and shows cost and latency. A candidate not approved for the route's data class is refused. Tagged NFR-11.
- A build log entry is appended, including how estimators should fill and submit the template.
