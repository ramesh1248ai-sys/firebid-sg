# TC-SYN-001 · Golden Reference Work Product Package

**Status: draft, not verified by a person.** Drafted 2026-10-08. See part 10 for who made
it and from what.

The machine-readable dataset is `golden.json`; the inputs and their provenance are in
`manifest.json`. This document is parts 1 to 5 and 7 to 11 of the package
(`docs/plan/TEST_STRATEGY.md`, section 4).

All positions and lengths are in millimetres in the building's own coordinates (the DXF
model space), unless a line says "on paper".

## 1. Test case summary

| | |
| --- | --- |
| **Test case ID** | TC-SYN-001 |
| **Objective** | Check stages 1 to 7 (intake to takeoff) on a small wet-pipe sprinkler tender whose sheets repeat each other, so that every count and length is known and duplicates must be settled |
| **Input** | Three DXF drawings from one consultant: a general arrangement at 1:100, an enlarged plan of the riser area at 1:50, and a riser schematic, not to scale |
| **Expected business outcome** | A takeoff that counts the installation once: 24 sprinklers, 2 valves, 1 reducer, 88.3 m of drawn pipe in three sizes, with the rule-derived drops, riser, tees and hangers identified as derived |
| **Key assumptions** | One floor (level 5); one riser; every measurement rule at its seeded default; no specification, so nothing says grooved pipework or seismic restraint |
| **Rules and standards** | `backend/config/measurement_rules.yaml`, version 1 of each rule. No standard is tested by this case |
| **Expected final result** | Part 4 |

Not in this case: specification, bill of quantities, pricing, clarifications, risk and
review (stages 8 to 12). TC-SYN-002 covers those.

## 2. Golden intermediate work products

### Stage 1 · Document intake

- **Purpose:** accept each file, recognise its kind and register its sheets.
- **Input used:** `FP-L05-201.dxf`, `FP-L05-301.dxf`, `FP-SCH-001.dxf`.
- **Expected processing:** each is a DXF with one layout to read, so each gives one sheet.
  Nothing is refused or quarantined.

| File | Kind | Sheets | State |
| --- | --- | --- | --- |
| FP-L05-201.dxf | dxf | 1 | done |
| FP-L05-301.dxf | dxf | 1 | done |
| FP-SCH-001.dxf | dxf | 1 | done |

- **Validation rules:** 3 documents, 3 sheets; no sheet with a reading error; every
  document ends in `done`.
- **Expected exceptions:** none.

### Stage 2 · Title blocks and registers

- **Purpose:** read each sheet's title block and build the drawing register.
- **Input used:** the title block at the bottom right of each sheet (labelled cells
  DRAWING NO., REV, SCALE, DATE, DRAWING TITLE, PROJECT) and the revision history above it.
- **Expected processing:** the REV cell gives the sheet's revision. The revision history
  lists one issue only (R01, 01.05.2026), so the date is that issue's. Each drawing number
  appears once, so each sheet is the current revision.

| Drawing | Title | Rev | Date | Scale | Level | Status |
| --- | --- | --- | --- | --- | --- | --- |
| FP-L05-201 | LEVEL 5 SPRINKLER LAYOUT PLAN | R01 | 2026-05-01 | 1:100 | L05 | Current |
| FP-L05-301 | ENLARGED PLAN - RISER AREA | R01 | 2026-05-01 | 1:50 | L05 | Current |
| FP-SCH-001 | SPRINKLER RISER SCHEMATIC | R01 | 2026-05-01 | NTS | none | Current |

Consultant: ALPHA CONSULTANTS PTE LTD. Project: PROPOSED COMMERCIAL DEVELOPMENT AT MARINA BAY.

- **Evidence:** each value is the text of its labelled cell on that sheet. The level of
  FP-L05-301 comes from its drawing number; its title names none.
- **Validation rules:** exactly one current revision for each drawing number; no
  superseded sheet; revision is `R01`, not a row of the history table misread.
- **Expected exceptions:** none.

### Stage 3 · Views and scale

- **Purpose:** find each sheet's views, the scale each states, and whether anything on the
  sheet proves it.
- **Input used:** view titles with the scale beneath; gridlines and their bubbles;
  dimensions; the words "NOT TO SCALE".
- **Expected processing:**
  - The grid is 6,000 mm in both directions: lines A to E at x = 500, 6,500, 12,500,
    18,500, 24,500 and lines 1 to 4 at y = 500, 6,500, 12,500, 18,500.
  - FP-L05-201 dimensions its grid and head spacing, so its stated 1:100 is proved by its
    own dimensions.
  - FP-L05-301 has no dimension. It shows gridlines A and B and 1, 2 and 3, which
    FP-L05-201 also has at a proved scale, so its 1:50 is proved by the shared grid.
  - FP-SCH-001 says NOT TO SCALE and its title block says NTS: nothing on it is measured.

| Sheet | View | Kind | Scale | Verdict | Proved by | Measurable |
| --- | --- | --- | --- | --- | --- | --- |
| FP-L05-201 | LEVEL 5 SPRINKLER LAYOUT PLAN | plan | 1:100 | verified | its own dimensions | yes |
| FP-L05-301 | ENLARGED PLAN - RISER AREA | plan | 1:50 | verified | the grid shared with FP-L05-201 | yes (ambiguity A2) |
| FP-SCH-001 | SPRINKLER RISER SCHEMATIC | schematic | NTS | not to scale | | no |

- **Validation rules:** one view on each sheet; a measurable view has a scale; a sheet
  marked NTS has no measurable view; the legend on FP-L05-201 is not a view.
- **Expected exceptions:** FP-SCH-001 is flagged not to scale.

### Stage 4 · Legends and symbols

- **Purpose:** read each legend row and say what object type its symbol is.
- **Input used:** the LEGEND at the top right of FP-L05-201: eight rows, each a symbol and
  its description. The other two sheets have no legend.
- **Expected processing:** each description names its type directly. The symbols on
  FP-L05-301 and FP-SCH-001 are the same consultant's and take the legend of FP-L05-201.
  No symbol on any sheet is without a legend row. No symbol has letters in it.

| # | Block | Legend says | Object type | Taken off by |
| --- | --- | --- | --- | --- |
| 0 | SPK-PEND | PENDENT SPRINKLER | `sprinkler_pendent` | count |
| 1 | SPK-UP | SPRINKLER - UP TYPE | `sprinkler_upright` | count |
| 2 | SPK-SW | SIDEWALL SPRINKLER | `sprinkler_sidewall` | count |
| 3 | VLV-GATE | GATE VALVE | `gate_valve` | count |
| 4 | VLV-CHK | NON-RETURN VALVE | `check_valve` | count |
| 5 | DEV-FS | FLOW SWITCH | `flow_switch` | count |
| 6 | FTG-RED | REDUCER | `fitting` (a reducer) | count |
| 7 | RSR | SPRINKLER RISER | `pipe` (a riser) | length |

- **Validation rules:** eight rows, in this order, on FP-L05-201 only; every row has an
  object type from the library; nothing is counted from a row until a person confirms it.
- **Expected exceptions:** FLOW SWITCH is in the legend and installed nowhere. That is a
  finding to report, not an error. "SPRINKLER - UP TYPE" does not contain the word
  "upright": a reader that proposes `sprinkler_pendent` for it has made a critical error.

### Stage 5 · Object detection

- **Purpose:** find every installed symbol on every sheet and say what it is.
- **Input used:** the symbols outside the legend and the title block, with stage 4's types.
- **Expected processing:** the riser symbol is pipe, not a counted object. The eight legend
  examples are not installed.

| Sheet | Pendent | Upright | Sidewall | Gate valve | Check valve | Fitting (reducer) | Riser (not counted) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| FP-L05-201 | 16 | 4 | 4 | 1 | 1 | 1 | 1 |
| FP-L05-301 | 4 | 0 | 0 | 1 | 1 | 0 | 1 |
| FP-SCH-001 | 0 | 0 | 0 | 1 | 1 | 0 | 0 |

Where they are, on FP-L05-201:

- Heads are on six branches at x = 3,000, 6,000, 9,000, 12,000, 15,000 and 18,000, four on
  each at y = 6,000, 9,000, 12,000 and 15,000. The first four branches carry pendents, the
  fifth uprights, the sixth sidewalls.
- On the main at y = 3,000: the riser at x = 1,000, the gate valve at 1,800, the check
  valve at 2,500 and the reducer at 10,500.

FP-L05-301 shows the riser, both valves and the pendents at (3,000 and 6,000) × (6,000 and
9,000). FP-SCH-001 shows the gate valve and the check valve on a vertical line.
`golden.json` lists all 35 instances with their positions and grid bays.

- **Evidence:** drawing number, position in the building, grid bay (for example the head at
  3,000, 6,000 is in bay A-B/1-2).
- **Validation rules:** no instance inside the legend's box or the title block; every
  instance has a position; the counts on FP-L05-201 are the installation's.
- **Expected exceptions:** none. There is no symbol without a legend row.

### Stage 6 · Pipe network

- **Purpose:** measure the pipe on each measurable sheet, by nominal diameter.
- **Input used:** the lines on the pipe layer; the size annotations "150Ø", "DN100" and
  "DN50"; stage 3's scales.
- **Expected processing:** the main runs east from the riser at y = 3,000. It is DN150 to
  the reducer and DN100 beyond it, to a stub end at x = 19,000. Six DN50 branches run north
  from the main to their last head at y = 15,000. As drawn, the main stops at the body of
  each valve and fitting and starts again beyond it.

| Sheet | DN150 | DN100 | DN50 | How |
| --- | ---: | ---: | ---: | --- |
| FP-L05-201 | 8,050 | 8,250 | 72,000 | 350 + 200 + 7,500; 8,250; 6 × 12,000 |
| FP-L05-301 | 4,300 | 0 | 13,000 | 350 + 200 + 3,750; none shown; 2 × 6,500 |
| FP-SCH-001 | not measured | | | not to scale |

- **Evidence:** each size is the annotation written on or along its run.
- **Validation rules:** every measured run has a size; every branch meets the main; the
  lengths on FP-L05-301 are no more than those on FP-L05-201.
- **Expected exceptions:** FP-SCH-001's pipe is not measured. Ambiguity A6 applies to the
  main's lengths.

### Stage 7 · Takeoff

- **Purpose:** one quantity for each item of the installation: duplicates between sheets
  settled, and the items the rules derive added and marked as derived.
- **Input used:** stages 5 and 6; `measurement_rules.yaml`; the note "CEILING HEIGHT 2750"
  on FP-L05-201.
- **Expected processing:**
  - Everything on FP-L05-301 is also on FP-L05-201, at the same place in the building: it
    is counted from FP-L05-201 and not again.
  - The valves on FP-SCH-001 are the riser's valves, drawn again: not counted again.
  - Drop length = branch elevation − ceiling height − sprinkler setting
    = 3,300 − 2,750 − 50 = 500 mm, with the ceiling height read from the drawing.
  - Riser = 4,000 mm floor to floor × 1 level.
  - A tee where each branch leaves the main, as none is drawn.
  - Hangers: one for each spacing, or part of one, of the pipe at a size.

Drawn items, counted once:

| Item | Size | Quantity |
| --- | --- | ---: |
| Sprinkler, pendent | | 16 no |
| Sprinkler, upright | | 4 no |
| Sprinkler, sidewall | | 4 no |
| Gate valve | DN150 | 1 no |
| Check valve | DN150 | 1 no |
| Reducer | 150 × 100 | 1 no |
| Pipe, main | DN150 | 8.05 m |
| Pipe, main | DN100 | 8.25 m |
| Pipe, branch | DN50 | 72.00 m |

Derived by rule, each "to be confirmed":

| Item | Size | Quantity | Rule | Calculation |
| --- | --- | ---: | --- | --- |
| Pipe, sprinkler drop | DN25 | 8.0 m | `drop_length` | 16 pendents × 500 mm (ambiguity A1) |
| Pipe, riser | DN150 | 4.0 m | `riser_length` | 4,000 × 1 (ambiguity A3) |
| Tee | 150 × 50 | 3 no | `fitting_tee` | branches at 3,000, 6,000, 9,000 |
| Tee | 100 × 50 | 3 no | `fitting_tee` | branches at 12,000, 15,000, 18,000 |
| Hanger | DN50 | 24 no | `hanger_spacing` | 72,000 ÷ 3,000 |
| Hanger | DN100 | 3 no | `hanger_spacing` | 8,250 ÷ 4,000 = 2.06, rounded up |
| Hanger | DN150 | 2 no | `hanger_spacing` | 8,050 ÷ 4,500 = 1.79, rounded up |

Not expected: a derived reducer (one is drawn); an elbow (A4); grooved couplings; seismic
restraint; a flow switch; a pipe allowance at this stage (A5).

- **Validation rules:** each drawn count equals FP-L05-201's; the sum of the three sheets
  (20 pendents, 3 gate valves, 3 check valves) is not the takeoff; every item names the
  sheet and place, or the rule and version, it comes from; every derived item is marked.
- **Expected exceptions:** the duplicate groups are raised for a person to settle or are
  settled with their reason shown.

## 3. Stage-to-stage traceability

| Stage | Input | Output artifact | Used by | Key validation |
| --- | --- | --- | --- | --- |
| 1 Intake | Three DXF files | 3 documents, 3 sheets | 2, 3 | Every file accepted, one sheet each |
| 2 Title blocks | Title block cells | The register: number, revision, current | 7 (current sheets only) | One current revision for each number |
| 3 Views and scale | View titles, grid, dimensions | Views with scale and verdict | 5 (where a symbol is), 6 (lengths) | NTS sheet not measurable |
| 4 Legends | The legend on FP-L05-201 | 8 rows with object types | 5 | Every row typed; "UP TYPE" is upright |
| 5 Detection | Symbols, stage 4's types | Counts and positions for each sheet | 7 | Legend examples excluded |
| 6 Pipe network | Pipe lines, annotations, stage 3's scale | Length by size for each sheet | 7 | Every run sized |
| 7 Takeoff | Stages 2, 5, 6 and the rules | Items counted once, derived items | The bill (not in this case) | Duplicates settled; derived items marked |

Every final quantity traces back: for example "pendent sprinkler, 16" ← stage 5 on
FP-L05-201 (16 instances at named positions) ← legend row 0 (PENDENT SPRINKLER) ← the
current revision R01 of FP-L05-201 ← `FP-L05-201.dxf`.

## 4. Golden final output

**Expected result.** The installation, once:

| | |
| --- | ---: |
| Sprinklers | 24 (16 pendent, 4 upright, 4 sidewall) |
| Valves | 2 (1 gate, 1 check) |
| Reducer | 1 |
| Drawn pipe | 88.30 m (8.05 m DN150, 8.25 m DN100, 72.00 m DN50) |

**Supporting evidence.** FP-L05-201 R01 for every drawn item; the rules for derived items.

**Calculations.** Drawn pipe 8,050 + 8,250 + 72,000 = 88,300 mm. Sprinklers 4 × 4 + 4 + 4 = 24.

**Business rules applied.** Current revisions only; an item on two sheets is counted once;
a not-to-scale sheet is not measured; derived items by `measurement_rules.yaml` version 1.

**Exceptions and risks.** The six ambiguities in part 11. All derived quantities rest on
defaults marked "to be confirmed".

## 5. Comparison criteria

| Type | Fields | Rule |
| --- | --- | --- |
| A. Exact | File kinds and sheet counts; drawing number, revision, status; scale and verdict; each legend row's object type; every count; hanger and tee counts | Equal |
| B. Semantic | Legend descriptions; view titles; an item's description | Same meaning; case and spacing ignored |
| C. Tolerance | Pipe lengths: within 5%. Instance positions: within 250 mm. View extents: within 5 mm on paper | As stated |
| D. Evidence | Each counted item names its sheet and position; each derived item its rule and version; the drop cites the ceiling note | The cited source is the reference's |
| E. Completeness | 8 legend rows; 35 instances over three sheets; 9 drawn items; 7 derived items; 2 duplicate groups | Nothing missing; anything extra is listed for review |

Acceptable pipe ranges at 5%: DN150 7.65 to 8.45 m, DN100 7.84 to 8.66 m, DN50 68.4 to
75.6 m. A6 says what to do when the main falls outside its range.

## 7. Defect classification

The classes are those of the test strategy, section 7. For this case:

| Class | Examples |
| --- | --- |
| Critical | A sprinkler count other than 16, 4 and 4; duplicates not settled (20 pendents, 3 gate valves); the legend examples counted; "UP TYPE" mapped to pendent; FP-SCH-001's pipe measured |
| High | A drawing number or revision wrong; FP-L05-301 read at 1:100; a pipe length outside 5%; a branch with no size; the reducer missed |
| Medium | A weak or missing reason for a duplicate; a view's extent off; the level of a sheet missing |
| Low | Wording of a description; order of rows |
| Acceptable variation | Dates in another format; the reducer as a fitting with or without its attribute |

## 8. Evaluation score

The weights are the test strategy's. For this case, which has no stages 8 to 12:

| Dimension | Weight | Checks in this case |
| --- | ---: | --- |
| Data extraction accuracy | 20% | Stages 1, 2 and 4: 3 documents, 3 register rows, 8 legend rows |
| Intermediate work product accuracy | 20% | Stages 3, 5 and 6: 3 views, 35 instances, 5 lengths |
| Business rule accuracy | 20% | Stage 7: 2 duplicate groups, 7 derived items, 6 items not expected |
| Calculation accuracy | 15% | Stage 7: 9 drawn quantities and the derived quantities |
| Evidence and traceability | 10% | Every item's source |
| Completeness | 10% | The counts in part 5, row E |
| Final output quality | 5% | Part 4 |

A dimension's score is the share of its checks that pass. Report the overall weighted
score with the number of defects in each class and of acceptable variations.

## 9. AI-specific evaluation

| Check | In this case |
| --- | --- |
| Hallucination | Any object type other than the six installed; a flow switch counted; a size other than 150, 100 or 50 on a drawn run; a second revision |
| Grounding | Each count opens to its symbols on FP-L05-201; the drop length cites the ceiling note |
| Consistency | Reading the three files twice gives the same work products. All of stages 1 to 7 here are deterministic except the proposal for "SPRINKLER - UP TYPE", which must give the same type |
| Completeness | The legend on a plan sheet is found; the sidewall heads, which are turned 90 degrees, are found; the reducer is found |
| Instruction following | Nothing is counted until the eight rows are confirmed by a person; derived items are marked "to be confirmed" |
| Agent handoff | Stage 4 gives stage 5 a type for every block; stage 3 gives stage 6 a scale for both plans; stage 2 gives stage 7 the current sheets |
| Error propagation | A wrong type in stage 4 moves 4 or 16 heads in stages 5 and 7. A scale of 1:100 on FP-L05-301 halves its lengths in stage 6 and may hide the duplicates in stage 7 |

## 10. Test oracle

- **Made from:** the fixture generators' source and their own truth objects
  (`firebid.evals.synthetic_qto`, `synthetic_network`, `synthetic_symbols`, `synthetic`),
  and `backend/config/measurement_rules.yaml`. The counts and lengths in stages 5 and 6
  were produced by running the generators and reading what they say they drew; the
  platform's readers were not called. Stages 1 to 4 and 7 were written by hand from the
  generators' source and the rules.
- **Made by:** Claude (Opus 5.5), in the same session that built and debugged the
  platform. **This is a weakness and is stated as one.** On the same day the author had
  seen the platform's Symbols page and workbench for a bid made from these files. No value
  here was taken from them. One remembered figure differs from this reference (sprinkler
  drops of 12 m against 8.0 m here); the rule's own wording was kept and the difference is
  recorded as ambiguity A1 rather than adjusted either way.
- **Independence by construction:** the generator decides what is drawn before any reader
  sees it, so stages 5 and 6 do not depend on the author. Stage 7's derived items do
  depend on reading the rules, and are where a second person should look first.
- **Not yet done:** verification by a person; a comparison run against the platform (the
  comparison tooling is not built).

## 11. Known ambiguities and assumptions

| | Ambiguity | Expected | Alternative | Impact | To resolve |
| --- | --- | --- | --- | --- | --- |
| A1 | Which heads get a drop. The rule says "a pendent head drops from the branch" | Pendents only: 16 × 0.5 = 8.0 m | Every head: 24 × 0.5 = 12.0 m | 4.0 m of DN25 | Senior Estimator: do uprights and sidewalls take a drop or a sprig, and of what length |
| A2 | Whether FP-L05-301's scale is proved. It has no dimension and shows two gridlines one way and three the other | Verified, by the grid it shares with FP-L05-201 | Stated only, to be calibrated by a person | Whether its lengths can be used without a calibration; it does not change the takeoff, which counts from FP-L05-201 | Product owner: how many shared gridlines prove a scale |
| A3 | The riser's size. The symbol and "RISER R1" give none | DN150, the size of the main it feeds | No size, raised for a person | 4.0 m of DN150 | Estimator convention |
| A4 | An elbow where the riser turns into the main | None: no change of direction is drawn on plan | 1 elbow DN150 | 1 fitting | Estimator convention |
| A5 | The `allowance` rule gives pipe 5% | Not applied at takeoff: quantities are net, and the allowance belongs to the bill | Applied at takeoff: each pipe length × 1.05 | 5% on all pipe | Product owner: at which stage the allowance is applied |
| A6 | Whether the main is measured as drawn or through its valves and fittings. The drawing stops the pipe at each body; the bid's default convention measures "through fittings and valves" | As drawn: DN150 8.05 m, DN100 8.25 m | Through them: DN150 9.05 to 9.50 m, DN100 up to 8.50 m | Up to 1.45 m of DN150, outside the 5% tolerance | Senior Estimator: the convention. Until then a DN150 between 8.05 and 9.50 m is a difference to settle, not a defect |

Assumptions: one level is served by the riser; the ceiling height note applies to the whole
floor; the three files are the whole tender.
