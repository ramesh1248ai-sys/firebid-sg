# TC-SYN-003 · Golden Reference Work Product Package

**Status: draft, not verified by a person.** Drafted 2026-10-08. See part 10 for who made
it and from what.

The machine-readable dataset is `golden.json`; the inputs and their provenance are in
`manifest.json`. This document is parts 1 to 5 and 7 to 11 of the package
(`docs/plan/TEST_STRATEGY.md`, section 4).

All positions and lengths are in millimetres in the building's own coordinates (the DXF
model space), unless a line says metres.

## 1. Test case summary

| | |
| --- | --- |
| **Test case ID** | TC-SYN-003 |
| **Objective** | Check stages 1 to 7 on the systems that are not sprinklers: equipment counted exactly and once, its duty read from a schedule, pipe of three systems measured, and a schematic that repeats some equipment and is the only place another appears |
| **Input** | Four DXF drawings from one consultant: a fire pump room plan, a typical floor plan and a site hydrant plan at 1:100, and a riser schematic, not to scale |
| **Expected business outcome** | A takeoff of 17 pieces of equipment of 13 types, each once; 62.85 m of drawn pipe in four sizes over three systems; both fire pumps and the jockey pump with flow, head and power from the schedule |
| **Key assumptions** | Every measurement rule at its seeded default; no specification, so hangers are at the company default and nothing is braced |
| **Rules and standards** | `backend/config/measurement_rules.yaml`, version 1 of each rule. No standard is tested by this case |
| **Expected final result** | Part 4 |

Not in this case: specification, bill, pricing, clarifications, risk and review (stages 8 to
12). No synthetic fixture specifies, bills or prices this equipment; TC-SYN-002 covers
those stages for the sprinkler installation.

## 2. Golden intermediate work products

### Stage 1 · Document intake

Four DXF files, one sheet each, all accepted: `FP-B1-101.dxf`, `FP-L03-401.dxf`,
`FP-SITE-001.dxf`, `FP-SCH-002.dxf`.

- **Validation rules:** 4 documents, 4 sheets; none refused.

### Stage 2 · Title blocks and registers

| Drawing | Title | Rev | Date | Scale | Level | Status |
| --- | --- | --- | --- | --- | --- | --- |
| FP-B1-101 | FIRE PUMP ROOM LAYOUT PLAN | R01 | 2026-05-01 | 1:100 | B1 | Current |
| FP-L03-401 | LEVEL 3 WET RISER AND HOSE REEL LAYOUT PLAN | R01 | 2026-05-01 | 1:100 | L03 | Current |
| FP-SITE-001 | SITE PLAN - EXTERNAL HYDRANT LAYOUT | R01 | 2026-05-01 | 1:100 | none | Current |
| FP-SCH-002 | FIRE PROTECTION RISER SCHEMATIC | R01 | 2026-05-01 | NTS | none | Current |

Consultant: DELTA M&E CONSULTANTS PTE LTD. Project: PROPOSED COMMERCIAL DEVELOPMENT AT
MARINA BAY.

- **Evidence:** the pump room's level is in its drawing number only; the floor's is in
  both number and title.
- **Validation rules:** one current revision for each number; the pump room is B1.

### Stage 3 · Views and scale

| Sheet | View | Scale | Verdict | Measurable |
| --- | --- | --- | --- | --- |
| FP-B1-101 | Plan | 1:100 | Verified by its own dimensions | Yes |
| FP-L03-401 | Plan | 1:100 | Verified by its own dimensions | Yes |
| FP-SITE-001 | Plan | 1:100 | Verified by its own dimensions | Yes |
| FP-SCH-002 | Schematic | NTS | Not to scale | No |

Two things on these sheets are tables, not views, and later stages read them:

- **The FIRE PUMP SCHEDULE** above the pump room plan: columns TAG, DESCRIPTION, DUTY,
  FLOW (L/S), HEAD (M), POWER (KW); three rows.
- **The level schedule** on the schematic: L01 +0.000, L02 +4.500, L03 +9.000,
  L04 +13.500, L05 +18.000, ROOF +22.500. Every floor is 4,500 mm to the next.

- **Validation rules:** the schematic is not measured, but its text is still evidence; the
  schedule is not a legend and its rows are not symbols.
- **Expected exceptions:** FP-SCH-002 is flagged not to scale.

### Stage 4 · Legends and symbols

The same LEGEND of 14 rows is on each of the three plans. The schematic has none and takes
the consultant's.

| # | Block | Legend says | Object type |
| --- | --- | --- | --- |
| 0 | EQ-FP | FIRE PUMP | `fire_pump` |
| 1 | EQ-JP | JOCKEY PUMP | `jockey_pump` |
| 2 | EQ-PC | PUMP CONTROL PANEL | `pump_controller` |
| 3 | EQ-TK | FIRE WATER TANK | `fire_water_tank` |
| 4 | EQ-BI | BREECHING INLET | `breeching_inlet` |
| 5 | EQ-LV | LANDING VALVE | `landing_valve` |
| 6 | EQ-FH | PILLAR HYDRANT | `hydrant` |
| 7 | EQ-HR | HOSE REEL | `hose_reel` |
| 8 | EQ-TH | TEST HEADER | `test_header` |
| 9 | VS-DP | DRY PIPE VALVE SET | `dry_pipe_valve_set` |
| 10 | VS-PA | PRE-ACTION VALVE SET | `pre_action_valve_set` |
| 11 | VS-DL | DELUGE VALVE SET | `deluge_valve_set` |
| 12 | EQ-AC | AIR COMPRESSOR | `air_compressor` |
| 13 | RSR-W | WET RISING MAIN | `pipe` (a rising main) |

- **Validation rules:** 14 rows, the same on each plan; every type is installed on some
  sheet; no symbol is without a row.
- **Expected exceptions:** none. The three valve sets differ by one small shape inside the
  same bow-tie: taking one for another is a critical error. "FIRE PUMP" and "JOCKEY PUMP"
  both contain "PUMP", as "PUMP CONTROL PANEL" does: each must get its own type.

### Stage 5 · Object detection

| Sheet | Counted | Tags |
| --- | --- | --- |
| FP-B1-101 | 2 fire pumps, 1 jockey pump, 1 pump controller, 1 fire water tank, 1 test header, 1 air compressor, 1 dry pipe, 1 pre-action and 1 deluge valve set (10) | TK-01, FP-01, FP-02, JP-01, PC-01, TH-01, AC-01. The valve sets have none |
| FP-L03-401 | 1 landing valve, 2 hose reels (3) | LV-03, HR-31, HR-32 |
| FP-SITE-001 | 3 hydrants (3) | FH-1, FH-2, FH-3 |
| FP-SCH-002 | 1 breeching inlet, 5 landing valves, 5 hose reels, 2 fire pumps (13) | BI-01, FP-01, FP-02 |

The rising main's symbol is on the pump room plan at (14,000, 3,000) and on the floor plan
at (1,000, 3,000). It is pipe and is not counted. `golden.json` gives all 29 instances with
positions, tags and grid bays.

- **Validation rules:** no instance inside a legend, the schedule or the title block; each
  tag is the text beside its own symbol.
- **Expected exceptions:** none.

### Stage 6 · Pipe network

| Sheet | System | DN200 | DN150 | DN100 | DN50 | How |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| FP-B1-101 | Pump suction and discharge | 11,500 | 19,800 | | 2,750 | Suction header 8,750 + 2,750; two spurs, two pump discharges and the header 4 × 2,750 + 8,800; jockey discharge |
| FP-L03-401 | Wet rising main | | | 2,550 | | Rising main to the landing valve |
| FP-SITE-001 | Hydrant | | 26,250 | | | Main 18,000; three spurs of 2,750 |
| FP-SCH-002 | | not measured | | | | Not to scale |

As drawn, a pipe stops at the edge of the pump, tank, riser or hydrant symbol it meets (C4).

- **Validation rules:** every run has a size; nothing is measured on the schematic.

### Stage 7 · Takeoff

- **Expected processing:**
  - The schematic's fire pumps carry the tags FP-01 and FP-02 of the pump room's: the same
    two pumps, counted from the plan.
  - The schematic's landing valve and hose reel at L03 are the floor plan's.
  - The breeching inlet is on no plan: it is counted from the schematic.
  - Each pump takes its duty from its row of the schedule, by tag. Flow is stated in litres
    a second; 47.5 L/s is 2,850 L/min and 1.5 L/s is 90 L/min.
  - The landing valve is DN100, the size of the pipe it stands on. No pipe is drawn to the
    valve sets, the test header or the control panel, so they have no size.
  - Hangers at the company default spacing. The hydrant main is buried and is not hung.

**Equipment, each once: 17 pieces.**

| Item | Qty | From | Attributes |
| --- | ---: | --- | --- |
| Fire pump FP-01 | 1 | FP-B1-101 | Electric, duty, 2,850 L/min, 80 m, 75 kW |
| Fire pump FP-02 | 1 | FP-B1-101 | Diesel, standby, 2,850 L/min, 80 m, 75 kW |
| Jockey pump JP-01 | 1 | FP-B1-101 | 90 L/min, 85 m, 4 kW |
| Pump controller PC-01 | 1 | FP-B1-101 | |
| Fire water tank TK-01 | 1 | FP-B1-101 | |
| Test header TH-01 | 1 | FP-B1-101 | |
| Air compressor AC-01 | 1 | FP-B1-101 | |
| Dry pipe, pre-action, deluge valve set | 1 each | FP-B1-101 | |
| Landing valve LV-03 | 1 | FP-L03-401 | DN100 |
| Hose reel HR-31, HR-32 | 2 | FP-L03-401 | |
| Hydrant FH-1 to FH-3 | 3 | FP-SITE-001 | |
| Breeching inlet BI-01 | 1 | FP-SCH-002 | |

**Pipe, as drawn:** B1: 11.50 m DN200, 19.80 m DN150, 2.75 m DN50. L03: 2.55 m DN100.
Site: 26.25 m DN150. Total 62.85 m.

**Derived by rule,** each "to be confirmed":

| Item | Size | Qty | Level | Calculation |
| --- | --- | ---: | --- | --- |
| Rising main | DN100 | 4.5 m | L03 | 4,500 from the level schedule × 1 (C2) |
| Rising main | DN150 | 4.0 m | B1 | The rule's default 4,000 × 1: the schedule does not name B1 (C2) |
| Tee | 200 × 150 | 2 | B1 | The fire pump spurs leave the suction header (C3) |
| Tee | 150 × 150 | 1 | B1 | The standby pump joins the discharge header (C3) |
| Tee | 150 × 50 | 1 | B1 | The jockey pump joins the discharge header (C3) |
| Elbow | DN200 | 1 | B1 | The suction header turns to the jockey pump (C3) |
| Elbow | DN150 | 1 | B1 | The duty pump's discharge turns into the header (C3) |
| Tee | 150 × 150 | 3 | site | The hydrant spurs leave the main (C3) |
| Hanger | DN200 | 3 | B1 | 11,500 ÷ 4,500, rounded up |
| Hanger | DN150 | 5 | B1 | 19,800 ÷ 4,500, rounded up |
| Hanger | DN50 | 1 | B1 | 2,750 ÷ 3,000, rounded up |
| Hanger | DN100 | 1 | L03 | 2,550 ÷ 4,000, rounded up |

Not expected: a hanger on the hydrant main; a sprinkler drop; seismic restraint; a reducer.

- **Validation rules:** the sum of the sheets (4 fire pumps, 6 landing valves, 7 hose
  reels) is not the takeoff; each pump attribute cites its schedule row; every derived item
  names its rule and version.
- **Expected exceptions:** the duplicate groups are raised or settled with their reason.
  See C1: four levels on the schematic have no plan in the tender.

## 3. Stage-to-stage traceability

| Stage | Input | Output artifact | Used by | Key validation |
| --- | --- | --- | --- | --- |
| 1 Intake | Four DXF files | 4 documents, 4 sheets | 2, 3 | Every file accepted |
| 2 Title blocks | Title block cells | The register, with levels B1 and L03 | 7 | One current revision each |
| 3 Views and scale | View titles, dimensions, tables | Views; the pump schedule; the level schedule | 5, 6, 7 | The schedule is not a view or a legend |
| 4 Legends | The legend on each plan | 14 rows with object types | 5 | Three valve sets told apart |
| 5 Detection | Symbols, tags, stage 4's types | Counts, tags and positions | 7 | Each tag beside its own symbol |
| 6 Pipe network | Pipe lines, annotations | Length by size and system | 7 | The schematic is not measured |
| 7 Takeoff | Stages 2, 3, 5, 6 and the rules | Equipment once, with duties; pipe; derived items | The bill (not in this case) | Tags settle the pumps |

For example "fire pump FP-01, 2,850 L/min" ← the takeoff item ← the symbol tagged FP-01 at
(5,000, 6,000) on FP-B1-101 and its schedule row "FP-01 | ELECTRIC FIRE PUMP | DUTY | 47.5
| 80 | 75" ← legend row 0 (FIRE PUMP) ← the current revision R01 of `FP-B1-101.dxf`.

## 4. Golden final output

**Expected result.** 17 pieces of equipment, each once: 2 fire pumps, 1 jockey pump, 1 pump
controller, 1 fire water tank, 1 test header, 1 air compressor, 1 each of dry pipe,
pre-action and deluge valve set, 1 landing valve, 2 hose reels, 3 hydrants, 1 breeching
inlet. 62.85 m of drawn pipe.

**Calculations.** 11,500 + 19,800 + 2,750 + 2,550 + 26,250 = 62,850 mm. 10 + 3 + 3 + 1 = 17.

**Business rules applied.** Current revisions only; equipment drawn on a plan and again on
a schematic is counted once; a not-to-scale sheet is not measured but is read; derived
items by `measurement_rules.yaml` version 1.

**Exceptions and risks.** The seven ambiguities in part 11. C1 and C2 say this takeoff is
of the sheets given, and is short of the building the schematic shows.

## 5. Comparison criteria

| Type | Fields | Rule |
| --- | --- | --- |
| A. Exact | File kinds; drawing number, revision, level, status; scale and verdict; each legend row's type; **every equipment count**; each tag; each pump's flow, head and power | Equal |
| B. Semantic | Legend descriptions; view titles; item descriptions; the name of a system | Same meaning |
| C. Tolerance | Pipe lengths within 5%; positions within 250 mm | As stated |
| D. Evidence | Each item names its sheet and position; each pump attribute its schedule row; the L03 rising main the level schedule; each derived item its rule and version | The cited source is the reference's |
| E. Completeness | 14 legend rows; 29 instances over four sheets; 17 pieces of equipment; 5 pipe quantities; 3 schedule rows; 6 levels; 3 duplicate groups | Nothing missing; anything extra is listed for review |

## 7. Defect classification

| Class | Examples in this case |
| --- | --- |
| Critical | Any equipment count wrong: 4 fire pumps, 6 landing valves, a missing breeching inlet; one valve set taken for another; a jockey pump counted as a fire pump; a pump's flow, head or power wrong, or the diesel pump given the electric pump's row; the schematic's line measured |
| High | A drawing number, revision or level wrong; a pipe length outside 5%; a run with no size; the rising main at L03 on the 4,000 default when the level schedule gives 4,500; a hanger on the buried main |
| Medium | A tag missing or beside the wrong symbol; a weak reason for a duplicate; a system named so that pipe is put with the wrong equipment |
| Low | Wording; order |
| Acceptable variation | Flow shown as L/s or L/min with the row cited; the derived fittings until C3 is settled; a run measured to a symbol's centre (C4) |

## 8. Evaluation score

The weights are the test strategy's. For this case, which has no stages 8 to 12:

| Dimension | Weight | Checks in this case |
| --- | ---: | --- |
| Data extraction accuracy | 20% | Stages 1, 2 and 4: 4 documents, 4 register rows, 14 legend rows |
| Intermediate work product accuracy | 20% | Stages 3, 5 and 6: 4 views, 2 tables, 29 instances, 16 tags, 5 lengths |
| Business rule accuracy | 20% | Stage 7: 3 duplicate groups, 12 derived items, 5 items not expected |
| Calculation accuracy | 15% | Stage 7: 13 counts, 5 pipe quantities, 9 pump attributes |
| Evidence and traceability | 10% | Every item's source |
| Completeness | 10% | Part 5, row E |
| Final output quality | 5% | Part 4 |

## 9. AI-specific evaluation

| Check | In this case |
| --- | --- |
| Hallucination | An object type not among the 13; a pump attribute the schedule does not state (a make, a speed); a size on a valve set; a sprinkler |
| Grounding | Each pump attribute opens to its schedule row; the L03 rising main to the two levels of the schematic; each count to its symbols |
| Consistency | Read twice, the same work products. Everything here is deterministic except the type proposed for each legend row, which must be the same |
| Completeness | The breeching inlet, which is on the schematic only; the jockey pump's row, whose duty is "-"; the three untagged valve sets; the schedule above the plan |
| Instruction following | Nothing is counted until the 14 rows are confirmed by a person; derived items are marked "to be confirmed" |
| Agent handoff | Stage 3 gives stage 7 the schedule and the level heights; stage 5 gives stage 7 the tags that settle the pumps; stage 2 gives the levels the hangers are counted on |
| Error propagation | A missed tag at stage 5 leaves four fire pumps at stage 7 and a pump with no duty. A missed level schedule puts the rising main on the default. The schematic read as a plan adds its line as pipe and all 13 of its symbols |

## 10. Test oracle

- **Made from:** the fixture generator's source (`firebid.evals.synthetic_systems`) and
  `backend/config/measurement_rules.yaml`.
- **How each stage was made:** stages 5 and 6 are what the generator says it draws,
  recorded as it places each symbol and pipe (`work_out.py`); the platform's readers were
  not called. The legend, the schedule and the levels are the generator's own constants.
  Stages 1 to 3 and 7 were written by hand from the generator's source and the rules.
- **Made by:** Claude (Opus 5.5), in a session that had not seen the platform's output for
  this tender. **A weakness, stated as one:** the author had read
  `backend/tests/db/test_systems_takeoff.py`, which asserts some of what the platform
  gives for these sheets (the equipment counts, the pump's 2,850 L/min, the L03 rising
  main of 4,500 mm, the hydrant main of 26,250 mm). Each of those is also derivable from
  the generator alone and was derived that way here; they agree. What the tests do not
  state (the B1 rising main, the pump room's fittings, the hanger counts) is the author's
  own reading and is where a person should look first.
- **Independence by construction:** the generator decides what is drawn before any reader
  sees it, so stages 5 and 6 do not depend on the author.
- **Not yet done:** verification by a person; a comparison run against the platform.

## 11. Known ambiguities and assumptions

| | Ambiguity | Expected | Alternative | Impact | To resolve |
| --- | --- | --- | --- | --- | --- |
| C1 | The schematic shows a landing valve and a hose reel at each of five levels. The tender has a plan of L03 only, which shows one landing valve and two hose reels | Counted from the plans given: 1 landing valve, 2 hose reels. The four levels with no plan are reported as missing sheets | Counted for the building: 5 landing valves and 5 to 10 hose reels | 4 landing valves and 3 to 8 hose reels | Senior Estimator: is equipment on a schematic counted where no plan shows it. A real tender would have the other plans |
| C2 | The rising main's height. The rule gives each riser symbol one floor | 4.5 m of DN100 at L03 and 4.0 m of DN150 at B1: 8.5 m | The whole rise: 22.5 m from L01 to the roof by the level schedule, and the basement below it | At least 14 m of rising main | Senior Estimator; and whether B1 to L01 can be had from any sheet |
| C3 | The fittings of the pump room and the site main. The generator states no truth for them | 7 tees and 2 elbows, from where the drawn runs meet | Fewer, if a run that ends at equipment is given a fitting differently; more, if flanges or reducers at the pumps are derived | Up to about 10 fittings | Estimator convention. Until then differences are reported, not scored as defects |
| C4 | Whether a run is measured to the symbol's edge, as drawn, or to its centre | As drawn | To the centre: up to 250 mm more at each of 13 ends | Up to about 3.2 m over the tender, inside 5% | Senior Estimator. The same question as TC-SYN-001's A6 |
| C5 | The size of the B1 rising main: the symbol states none | DN150, the size of the header that ends at it | DN100, the size the floor plan draws from it | 4.0 m at one size or the other | Estimator convention |
| C6 | The name of the system the pump room's pipe belongs to | Fire pump suction and discharge | The rising main's system, or wet riser | A label; no quantity | Product owner |
| C7 | The valve sets, the test header and the control panel have no pipe drawn to them | Counted, with no size | Raised for a person as unconnected equipment | No quantity | Estimator convention |
| C8 | Whether the site plan, and what is taken off from it, has a level. Its drawing number is FP-SITE-001; it is of no storey | None: the sheet, the hydrants, the hydrant main and its tees have no level | A level named SITE, read from the drawing number, so that every item says where it is | No total. It moves 26.25 m of DN150 and 3 tees from no level to SITE, so it decides where a level's labour multiplier reaches, and whether the items pass the evidence check that asks for a level | Senior Estimator. Added 2026-10-09, after the comparison showed the platform naming SITE at the takeoff |

Assumptions: the four files are the whole tender; the rising main RM1 on the pump room
plan and on the floor plan is one main; flow converts exactly at 60 seconds a minute.
