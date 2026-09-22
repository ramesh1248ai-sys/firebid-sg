# P1-09 · BOQ Generation and Client BOQ Reconciliation

**Builds on:** P1-07 (verified QTO items with evidence), P0-04 (gateway). **Needs:** 3–5 real client BOQ workbooks from the golden set (business track). The synthetic workbook this step creates covers the tests.

Begin by replying with a plan that maps each item under **Done when** to the work that satisfies it. Wait for approval before changing files.

## Read first

- `docs/plan/project-context.md`: guardrails 1 and 3, *proposal*, money and units.
- Requirements: §6.6 (FR-BOQ-01 to 06), §6.16 (FR-ADM-03), NFR-13, Appendix B (the linked BOQ line example with a +7.0% variance).
- `docs/plan/BUILD_LOG.md`.

## Goal

Produce a traceable BOQ from verified quantities, in the company format or the client's own workbook. Reconcile the client's quantities against measured quantities, so variances and missing items surface before pricing.

## Scope

FR-BOQ-01, 02, 03, 04, 05, 06, FR-ADM-03, NFR-13 (xlsx).

## Build

1. **Company BOQ templates** (FR-ADM-03): a configurable structure (system → level/zone → item). Descriptions are generated from canonical type plus attributes (e.g. "150 mm galvanised steel pipe, Sch 40, grooved"). Roll-up rules decide what aggregates by level versus whole building.
2. **Generation** (FR-BOQ-01): build BOQ lines from verified QTO items. Each line links to its QTO items and, through them, to evidence. Units are m, nr, set or lot, with deterministic conversions.
3. **Client BOQ import** (FR-BOQ-02):
   - Read with openpyxl in read-only mode, inside the parser sandbox, keeping the original workbook bytes. openpyxl never writes a client workbook.
   - Detect header rows and columns (item number, description, unit, quantity, rate, amount) with heuristics. For ambiguous layouts, ask the model to propose a column mapping for the user to confirm.
   - Parse sections and lines into ClientBOQ and ClientBOQLine.
4. **Mapping** (FR-BOQ-02):
   - The model proposes mappings between QTO groups and client lines from description semantics, units, sizes and section context, with confidence, in batches.
   - The estimator confirms or corrects each mapping.
   - List unmapped client lines and unmapped QTO groups side by side.
5. **Reconciliation report** (FR-BOQ-03):
   - For each mapped line: client quantity, measured quantity, variance and variance percentage, with a configurable threshold.
   - Items measured but absent from the client BOQ.
   - Client lines with no measured quantity.
   - Evidence links on every row.
   - xlsx export.
   - Each variance above threshold is flagged as a clarification candidate, which P2-06 consumes.
6. **Export** (FR-BOQ-04): produce the priced client workbook by **patching the original file's XML**. Everything outside the target cells is copied byte for byte: styles, formulas, merged cells, hidden sheets, print settings, images, charts, comments, data validation, defined names and external links.
   - Unzip the original package and edit only the `<c>` elements of the target rate cells (and amount cells that hold plain values) in the affected worksheet parts.
   - Leave amount cells that contain formulas untouched, and set the workbook's full-recalculation-on-load flag so Excel recomputes them.
   - Write numbers as numeric cells, never as shared strings.
   - Re-zip with the original part order and compression.

   Export the company-format BOQ as a separate workbook, which openpyxl may create from scratch.
7. **Trace check** (FR-BOQ-05): every BOQ line traces to QTO items and evidence, or carries an explicit provisional-sum or lump-sum marker. The check runs at G1 and G2 and blocks either gate on failure.
8. **Measurement convention** (FR-BOQ-06): per-tender settings (e.g. pipe measured net along centreline; fittings enumerated or deemed included). These generate a qualification text snippet that later steps put in the tender qualifications.
9. **Synthetic client BOQ fixture:** a workbook in a typical Singapore QS layout, with:
   - sections, sub-totals, formulas, merged cells and a provisional-sums section;
   - an embedded image (e.g. a logo), a chart, cell comments, data validation lists and defined names.

   These extra objects are the ones a naive re-save loses.

## Done when

- Round trip: importing the synthetic client BOQ and exporting it priced gives a package whose parts are byte-identical to the original, except the patched worksheet parts and the calculation flag. Within the patched parts, only the target cells differ. The fixture includes an image, a chart, comments, data validation and defined names, and all survive. The exported file opens in Excel without a repair prompt (a manual check, recorded in the build log). Tagged FR-BOQ-04 and NFR-13.
- On the golden set when present, mapping accuracy is reported against the ≥ 90% target; unmapped lines are listed. Tagged FR-BOQ-02.
- A test with client quantity 120 m and measured 128.4 m reports a variance of +7.0% and flags it as a clarification candidate. Tagged FR-BOQ-03.
- The trace check blocks G1 and G2 when an unmarked BOQ line lacks trace, and passes once it is fixed or marked. Tagged FR-BOQ-05.
- BOQ generation from verified QTO matches expected lines and descriptions for the synthetic bid. Tagged FR-BOQ-01.
- Template edits and convention settings are tested. Tagged FR-ADM-03 and FR-BOQ-06.
- A build log entry is appended.
