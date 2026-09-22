# P1-02 · Classification, Registers and Revision Control

**Builds on:** P1-01 (documents and sheets), P0-04 (`title_block_reader` agent, gateway), P0-05 (eval harness, synthetic revisions). **Needs:** golden set title blocks for the accuracy measurement. Synthetic fixtures cover the logic.

Begin by replying with a plan that maps each item under **Done when** to the work that satisfies it. Wait for approval before changing files.

## Read first

- `docs/plan/project-context.md`: guardrail 6 (only Current revisions feed takeoff), leading word *proposal*.
- Requirements: §6.2 (FR-DOC-02 to FR-DOC-06), §7 (document/sheet revision model), §5 (stage S1 output).
- `docs/plan/BUILD_LOG.md`.

## Goal

Every document and sheet is classified. The drawing register and specification register show exactly one Current revision per sheet or document. Superseded and conflicting revisions are kept out of takeoff, and each sheet carries an input-quality verdict that sets expectations.

## Scope

FR-DOC-02, FR-DOC-03, FR-DOC-04, FR-DOC-05, FR-DOC-06.

## Build

1. **Title block extraction:**
   - Locate the title block from vector text density and frame geometry in typical regions.
   - Extract drawing number, title, revision, revision date, scale, discipline code, level and zone.
   - Use deterministic parsing first: per-consultant regular expressions, and drawing-number patterns such as `FP-L05-201`.
   - When parsing confidence is below threshold, call the `title_block_reader` agent (crop image plus text spans).
   - Once a person confirms a layout, remember it per consultant so later sheets parse deterministically.
2. **Document type classification** for non-drawing files: specification, BOQ, schedule, addendum, clarification response, contract conditions, other. Rules come first (file structure, keywords); the model handles the rest. Results are proposals, and low-confidence results create review tasks.
3. **Revision ordering:** a configurable scheme per project (e.g. `T1 < T2 < C1`, `A < B`, `P01 < P02`, `R03 < R04`), with transmittal or addendum date as the tiebreaker. When title block, filename and transmittal disagree, the sheet enters the **Conflict** state and waits for a person to resolve it.
4. **Registers** (FR-DOC-03):
   - Drawing register and specification register tables with filters (discipline, level, state) and xlsx export.
   - State transitions go through the P0-02 state machine, so Superseded sheets drop out of the Current set automatically.
   - The Estimator's "Register confirmed" action marks the stage S1 output.
5. **Addenda** (FR-DOC-05): mark an upload as an addendum (number, date). Link each changed sheet or clause to it. Expose an affected-items query that later steps extend to QTO and BOQ lines.
6. **Input quality** (FR-DOC-06), per sheet:
   - vector or raster class;
   - effective DPI for raster;
   - OCR mean confidence;
   - scale present or absent.

   Map these to an expected-accuracy band (high, medium, low) and a "manual takeoff recommended" flag, and show both in the register and the viewer.
7. **Eval integration:** implement the predictor for the `doc_classification` suite so `firebid-eval` measures drawing number and revision accuracy.

## Done when

- `firebid-eval run --suite doc_classification` reports drawing number and revision accuracy on synthetic fixtures, and on the golden set when present. The FR-DOC-02 target is ≥ 95% on vector title blocks. Record actuals in the build log.
- Seeded superseded sheets are excluded from the Current set in 100% of regression tests. Tagged FR-DOC-04.
- A fixture with disagreeing revision sources lands in Conflict, stays out of the Current set, and returns after a person resolves it.
- An addendum upload links its new revisions, and the affected-items query returns them. Tagged FR-DOC-05.
- The low-resolution raster fixture is flagged "manual takeoff recommended". Tagged FR-DOC-06.
- Register export opens in Excel with correct columns. Tagged FR-DOC-03.
- A build log entry is appended.
