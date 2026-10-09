# TC-SYN-002 · Golden Reference Work Product Package

**Status: draft, not verified by a person.** Drafted 2026-10-08. See part 10 for who made
it and from what.

The machine-readable dataset is `golden.json`; the inputs and their provenance are in
`manifest.json`. This document is parts 1 to 5 and 7 to 11 of the package
(`docs/plan/TEST_STRATEGY.md`, section 4).

Positions and lengths are in millimetres in the building's own coordinates (the DXF model
space) unless a line says metres. Money is in SGD, exclusive of GST unless a line says
otherwise.

## 1. Test case summary

| | |
| --- | --- |
| **Test case ID** | TC-SYN-002 |
| **Objective** | Check all twelve stages, from intake to an estimate under review, on one small tender whose specification contradicts its drawing, whose client bill differs from the takeoff, and whose rate list has gaps |
| **Input** | One DXF plan of a basement car park at 1:100 with two general notes; a particular specification (Word, 44 clauses, sections 1 to 8); the client's bill of quantities (Excel, 14 lines); the organisation's rate list (10 entries) and productivity list (8 entries) |
| **Expected business outcome** | A takeoff of 24 sprinklers, 2 valves, 1 reducer and 88.3 m of drawn pipe; 6 specification issues, 2 bill variances and 8 risks raised; an estimate of SGD 6,378.11 before GST and SGD 6,952.14 with it, with 6 lines unpriced and shown as such |
| **Key assumptions** | The scenario below; every measurement rule, convention, template and labour table at its seeded default |
| **Rules and standards** | The files under `backend/config/` named in `manifest.json`. GST 9%. No standard is tested by this case |
| **Expected final result** | Part 4 |

### The scenario

A stage's work product depends on what people have decided before it. This case fixes that.

| | |
| --- | --- |
| Priced on | 4 October 2026 |
| Submission deadline, validity | 15 October 2026, 90 days: prices must hold to 13 January 2027 |
| Measurement conventions | The defaults: pipe net along the centreline; fittings enumerated; hangers deemed included; drops measured by rule |
| **People have** | Confirmed the eight legend rows as the legend describes them. Verified every takeoff item as proposed, and approved G1. Built the company bill. Imported the rate list and the productivity list and priced the bill by rule. Entered a margin of 8% on cost |
| **People have not** | Verified any specification attribute, obligation or issue. Confirmed any proposed rate match or labour multiplier. Resolved a checklist item, treated a risk or issued a clarification. Carried the client's provisional sums into the bill. Decided G2, G3 or G4 |

Not in this case: more than one sheet, so no duplicates between sheets (TC-SYN-001 has
those); equipment and the other systems (TC-SYN-003); gates G2 to G4 and the frozen
submission.

## 2. Golden intermediate work products

### Stage 1 · Document intake

- **Purpose:** accept each file, recognise what it is, and register its sheets or lines.
- **Expected processing:** three tender documents, each of a different type. The rate list
  and the productivity list are the organisation's libraries, imported once, not documents
  of the tender.

| File | Type | Result |
| --- | --- | --- |
| FP-B1-201.dxf | Drawing | 1 sheet |
| Particular Specification Fire Protection.docx | Specification | 44 numbered clauses, 10 of them headings with no text |
| Bill of Quantities - Fire Sprinkler.xlsx | Client bill | Sheet "Bill 1 - Sprinklers" read. "Summary" has no measured lines. "Lists" is hidden and is not read |
| rates.xlsx | Rate library | 10 entries, none refused |
| productivity.xlsx | Productivity library | 8 entries, none refused |

- **Validation rules:** nothing refused or quarantined; the workbook's hidden sheet gives
  no bill line; every rate and productivity entry has a source.
- **Expected exceptions:** none.

### Stage 2 · Title blocks and registers

| Drawing | Title | Rev | Date | Scale | Level | Status |
| --- | --- | --- | --- | --- | --- | --- |
| FP-B1-201 | BASEMENT 1 CAR PARK SPRINKLER LAYOUT PLAN | R01 | 2026-05-01 | 1:100 | B1 | Current |

Consultant: ALPHA CONSULTANTS PTE LTD. Project: PROPOSED COMMERCIAL DEVELOPMENT AT MARINA
BAY. The specification is "PARTICULAR SPECIFICATION FOR FIRE PROTECTION SERVICES",
Revision Rev B, current.

- **Evidence:** each value is the text of its labelled cell in the title block. The level
  is in both the drawing number and the title.
- **Validation rules:** one current revision; the level is B1, which later stages use.
- **Expected exceptions:** none.

### Stage 3 · Views and scale

One plan view at 1:100, proved by the sheet's own grid and head-spacing dimensions, and
measurable. The grid is 6,000 mm both ways (lines A to E, 1 to 4), as in TC-SYN-001.

Two general notes are on the sheet and are read as notes, not as a view or a legend:

- ALL SPRINKLER PIPEWORK TO BE BLACK STEEL
- PIPES DN65 AND ABOVE: WELDED JOINTS

- **Validation rules:** one view; the legend is not a view; both notes are held with the
  sheet and its revision, because stage 8 cites them.

### Stage 4 · Legends and symbols

The same consultant's legend as TC-SYN-001, on this sheet: eight rows.

| # | Block | Legend says | Object type |
| --- | --- | --- | --- |
| 0 | SPK-PEND | PENDENT SPRINKLER | `sprinkler_pendent` |
| 1 | SPK-UP | SPRINKLER - UP TYPE | `sprinkler_upright` |
| 2 | SPK-SW | SIDEWALL SPRINKLER | `sprinkler_sidewall` |
| 3 | VLV-GATE | GATE VALVE | `gate_valve` |
| 4 | VLV-CHK | NON-RETURN VALVE | `check_valve` |
| 5 | DEV-FS | FLOW SWITCH | `flow_switch` |
| 6 | FTG-RED | REDUCER | `fitting` (a reducer) |
| 7 | RSR | SPRINKLER RISER | `pipe` (a riser) |

- **Expected exceptions:** FLOW SWITCH is in the legend and installed nowhere. In this case
  the client's bill has one (item C3), so the finding matters at stage 9.

### Stage 5 · Object detection

| Sheet | Pendent | Upright | Sidewall | Gate valve | Check valve | Fitting (reducer) | Riser (not counted) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| FP-B1-201 | 16 | 4 | 4 | 1 | 1 | 1 | 1 |

Heads are on six branches at x = 3,000 to 18,000 in steps of 3,000, four on each at
y = 6,000, 9,000, 12,000 and 15,000: pendents on the first four branches, uprights on the
fifth, sidewalls on the sixth. On the main at y = 3,000: the riser at x = 1,000, the gate
valve at 1,800, the check valve at 2,500, the reducer at 10,500. `golden.json` lists all 27
instances with positions and grid bays.

- **Validation rules:** the eight legend examples are not installed; no flow switch.

### Stage 6 · Pipe network

| Sheet | DN150 | DN100 | DN50 | How |
| --- | ---: | ---: | ---: | --- |
| FP-B1-201 | 8,050 | 8,250 | 72,000 | 350 + 200 + 7,500; 8,250; 6 × 12,000 |

Sizes are read from "150Ø" and "DN100" on the main and "DN50" along each branch. Ambiguity
B2 applies to the main.

### Stage 7 · Takeoff

- **Expected processing:**
  - One sheet, so nothing is counted twice and no duplicate group is raised.
  - The sheet states no ceiling height, so the drop takes every default of the rule:
    3,300 − 2,800 − 50 = 450 mm. (TC-SYN-001's sheet states 2,750 and gets 500 mm.)
  - Riser = 4,000 mm × 1 level. A tee where each branch leaves the main.
  - Hangers at the company default spacing, because the specification has no supports
    clause: 3,000 mm to DN50, 4,000 to DN100, 4,500 above.
  - Every item is on level B1.

Drawn:

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
| Pipe, sprinkler drop | DN25 | 7.2 m | `drop_length` | 16 pendents × 450 mm (B1) |
| Pipe, riser | DN150 | 4.0 m | `riser_length` | 4,000 × 1 (B3) |
| Tee | 150 × 50 | 3 no | `fitting_tee` | branches at 3,000, 6,000, 9,000 |
| Tee | 100 × 50 | 3 no | `fitting_tee` | branches at 12,000, 15,000, 18,000 |
| Hanger | DN50 | 24 no | `hanger_spacing` | 72,000 ÷ 3,000 |
| Hanger | DN100 | 3 no | `hanger_spacing` | 8,250 ÷ 4,000, rounded up |
| Hanger | DN150 | 2 no | `hanger_spacing` | 8,050 ÷ 4,500, rounded up |

**Attributes.** Every pipe material, class and joining method, and every sprinkler
K-factor, temperature, response and finish, is "not specified": the symbols state none, and
no specification attribute has been verified. An item that carries "black steel", "grooved"
or "K80" at this point has taken a proposal as a decision, which is a defect.

Not expected: a grooved coupling (clause 2.1.2 is unverified, and the drawing says welded);
seismic restraint; a flow switch; a test header (clause 7.6 requires one, which is an issue
at stage 8, not an item); an elbow (B4).

### Stage 8 · Specification

- **Purpose:** read the clauses; propose attributes and obligations with their clause; set
  the specification against the drawing; build the scope matrix.
- **Expected processing:** sections 1 to 4 and 6 to 8 are fire protection. Section 5 is low
  voltage electrical: nothing is extracted from clause 5.1, though it says "galvanised".

**Attributes: 29**, all proposed, each with its clause (the full list is in `golden.json`).
In short:

| System | What the specification says | Clause |
| --- | --- | --- |
| Sprinkler pipe to DN50 | Black steel, BS EN 10255, Heavy; screwed | 2.1.1, 2.1.2 |
| Sprinkler pipe DN65 and above | Black steel, ASTM A53 Grade B, Schedule 40; grooved | 2.1.1, 2.1.2 |
| Sprinkler pipe in the basement car park | Galvanised steel (a condition, not a size range) | 2.1.3 |
| Sprinkler heads | Pendent, quick response, K80, 68°C, chrome | 2.2.1 |
| Sidewall heads | Quick response, K80, 68°C, white | 2.2.2 |
| Approved makes | Tyco, Viking, Reliable | 2.2.3 |
| Hose reel pipe | Galvanised steel, BS EN 10255, Medium; screwed | 3.1 |
| Hydrant mains DN100 and above | Ductile iron, BS EN 545; flanged | 4.1 |

**Obligations: 13.**

| Category | Clause | Quantity |
| --- | --- | --- |
| Testing | 6.1 | 14 bar for 2 hours |
| Flushing | 6.1 | |
| Painting | 6.2 | 2 coats |
| Identification | 6.2 | |
| Commissioning | 6.3 | |
| Warranty | 6.4 | 12 months |
| Defects liability | 6.4 | 12 months |
| Maintenance | 6.4 | 12 months |
| Spares | 6.5 | 2 sets |
| Training | 6.5 | 2 days |
| Submittals | 6.6 | within 4 weeks |
| Authority | 6.7 | |
| Approved makes | 6.8 | |

**Issues against the drawing: 6**, each citing both sides.

| Issue | Specification | Drawing FP-B1-201 R01 |
| --- | --- | --- |
| Conflict: pipe material | 2.1.3: galvanised in the basement car park | Note: ALL SPRINKLER PIPEWORK TO BE BLACK STEEL |
| Conflict: joining method | 2.1.2: 65 mm and above grooved | Note: PIPES DN65 AND ABOVE: WELDED JOINTS |
| Missing from the drawings | 7.6: a flow test header in the pump room | None drawn |
| Missing from the specification | No clause mentions an upright sprinkler | 4 upright sprinklers |
| Ambiguous clause | 7.7: fire stopping "where required" | |
| Ambiguous clause | 7.8: gauges of the make scheduled "or equal" | |

**Scope matrix**, the same eight rows for each system the specification has (sprinkler,
hose reel, hydrant):

| Interface | Status | Clause |
| --- | --- | --- |
| Power supply | By others | 7.1 |
| Water supply | Included | 7.2 |
| Builder's works | Excluded | 7.3 |
| Ceiling access | Unclear | 7.4 |
| Painting | Included | 6.2 |
| Fire alarm interface | By others | 7.5 |
| Fire stopping | Included | 7.7 |
| Drainage | Unclear | none: the specification is silent |

- **Validation rules:** every attribute, obligation and issue quotes words that are in its
  clause; everything is a proposal; the conflict on material is raised because the sheet is
  a basement car park, which needs stage 2's level and title.
- **Expected exceptions:** the six issues.

### Stage 9 · Bill of quantities

**Our bill**, one section, FIRE SPRINKLER INSTALLATION, from the company standard template.
Heads are one line for each level (here B1); everything else is one line for the building.

| Group | Line | Unit | Quantity | Allowance |
| --- | --- | --- | ---: | ---: |
| Sprinkler heads | Pendent sprinkler head | nr | 16 | 0% |
| | Upright sprinkler head | nr | 4 | 0% |
| | Sidewall sprinkler head | nr | 4 | 0% |
| Pipework | 150 mm pipe (main) | m | 8.05 | 5% |
| | 100 mm pipe (main) | m | 8.25 | 5% |
| | 50 mm pipe (branch) | m | 72.00 | 5% |
| | 25 mm pipe (sprinkler drop) | m | 7.20 | 5% |
| | 150 mm pipe (riser) | m | 4.00 | 5% |
| Fittings | Reducer, 150 mm | nr | 1 | 0% |
| | Tee, DN150xDN50 (rule-derived) | nr | 3 | 0% |
| | Tee, DN100xDN50 (rule-derived) | nr | 3 | 0% |
| Valves and ancillaries | 150 mm gate valve | nr | 1 | 0% |
| | 150 mm check valve | nr | 1 | 0% |
| Hangers and supports | Pipe hanger, DN50 / DN100 / DN150 (rule-derived) | nr | 24 / 3 / 2 | 0% |

Quantities are net. The allowance is carried beside each line and never added to it.

**The client's bill against ours.** The mapping is the fixture's own. Variance is measured
less client, as a percentage of the client's quantity; beyond 5% either way it is flagged.

| Client item | Client says | Qty | Ours | Measured | Variance | Flagged |
| --- | --- | ---: | --- | ---: | ---: | --- |
| A1 | Pendent sprinkler head, quick response, K80, 68°C, chrome | 16 | Pendent head | 16 | 0.0% | |
| A2 | Upright sprinkler head, quick response, K80, 68°C | 4 | Upright head | 4 | 0.0% | |
| A3 | Horizontal sidewall sprinkler head, K80, 68°C, white | 4 | Sidewall head | 4 | 0.0% | |
| B1 | 150 mm diameter pipe, grooved joints | 8.0 | 150 mm main | 8.05 | +0.6% | |
| B2 | 100 mm diameter pipe, grooved joints | 8.25 | 100 mm main | 8.25 | 0.0% | |
| B3 | 50 mm diameter pipe, screwed joints | 70 | 50 mm branch | 72.00 | +2.9% | |
| B4 | 25 mm diameter sprinkler drop, screwed joints | 12 | 25 mm drop | 7.20 | −40.0% | **Yes** |
| B5 | 150 mm diameter riser | 4 | 150 mm riser | 4.00 | 0.0% | |
| C1 | 150 mm gate valve, flanged, with supervisory switch | 1 | Gate valve | 1 | 0.0% | |
| C2 | 150 mm check valve, grooved | 1 | Check valve | 1 | 0.0% | |
| C3 | Flow switch, 150 mm, with retard | 1 | none | 0 | | **Yes** |
| C4 | 150 x 100 mm concentric reducer | 1 | Reducer | 1 | 0.0% | |
| D1 | Allow for testing and commissioning (provisional sum, 5,000) | | none | | | |
| D2 | Allow for liaison with SCDF (provisional sum, 3,000) | | none | | | |

In ours and not in the client's: both tees and the hangers. The client's section B heading
says its pipework rates include hangers, which agrees with the convention.

- **Validation rules:** every verified takeoff item is in exactly one of our lines; a
  client line maps to at most one of ours; C3 is not mapped to anything; D1 and D2 are
  recognised as sums and not as measured lines.
- **Expected exceptions:** B4 and C3 are flagged. B4 is flagged under either reading of B1
  (10.8 m gives −10.0%).

### Stage 10 · Pricing and labour

**Rates.** A line takes the rate list's entry for exactly its item. The tender's prices
must hold to 13 January 2027.

| Line | Qty | Rate | Amount | Source | Status |
| --- | ---: | ---: | ---: | --- | --- |
| Pendent head | 16 | 38.50 | 616.00 | Company standard CS-2026 | Priced |
| Upright head | 4 | 38.50 | 154.00 | Company standard CS-2026 | Priced |
| Sidewall head | 4 | 52.00 | 208.00 | Company standard CS-2026 | Priced |
| 150 mm main | 8.05 | 68.20 | 549.01 | Quotation Q-2026-1001, to 31 Oct 2026 | Priced; **ends before the tender validity** |
| 100 mm main | 8.25 | 45.60 | 376.20 | PO-889, to 30 Jun 2026 | Priced; **expired** (B5) |
| 50 mm branch | 72 | 21.35 | 1,537.20 | Company standard CS-2026 | Priced |
| 25 mm drop | 7.2 | 12.10 | 87.12 | Company standard CS-2026 | Priced |
| 150 mm riser | 4 | 68.20 | 272.80 | Quotation Q-2026-1001 | Priced; ends before the tender validity |
| Reducer 150 | 1 | 64.00 | 64.00 | Company standard CS-2026 | Priced |
| Gate valve 150 | 1 | 890.00 | 890.00 | Quotation Q-2026-1002 | Priced |
| Tee 100 × 50 | 3 | | | Q-2026-1003 at 41.80 names the brand Victaulic | **Proposed**, not priced |
| Tee 150 × 50 | 3 | | | No entry | **Unpriced** |
| Check valve 150 | 1 | | | No entry | **Unpriced** |
| Hangers | 29 | | | No entry | **Unpriced** (B7) |

Priced bill: **4,754.33**. Six lines have no price, and none of them is priced at zero.

**Labour rates**, from the 2025 table (208 productive hours a month; 10% of hours at 1.5
times; insurance 2% of wages; one supervisor to eight workers). SGD an hour:

| | Wage | Levy | Accom. | Transport | Insurance | Overtime | Supervision | **Hourly** |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Skilled | 12.5000 | 2.4038 | 2.1635 | 0.5769 | 0.2500 | 0.6250 | 2.8209 | **21.3401** |
| General | 8.6538 | 3.3654 | 2.1635 | 0.5769 | 0.1731 | 0.4327 | 2.8209 | **18.1863** |

A supervisor costs 22.5672 an hour, of which each worker carries an eighth. Pipefitter
(half skilled, half general): **19.7632**. Sprinkler fitter (60% skilled): **20.0786**.

**Labour hours** = quantity × the list's man-hours for the most specific entry. No
multiplier is applied, because none is confirmed.

| Line | Qty | h/unit | Entry | Hours | Trade | Cost |
| --- | ---: | ---: | --- | ---: | --- | ---: |
| Pendent head | 16 | 0.40 | Pendent sprinkler | 6.40 | Sprinkler fitter | 128.50 |
| Upright head | 4 | 0.50 | Sprinkler, any other type | 2.00 | Sprinkler fitter | 40.16 |
| Sidewall head | 4 | 0.50 | Sprinkler, any other type | 2.00 | Sprinkler fitter | 40.16 |
| 150 mm main | 8.05 | 0.62 | Steel pipe DN150 | 4.99 | Pipefitter | 98.62 |
| 100 mm main | 8.25 | 0.45 | Steel pipe DN100 | 3.71 | Pipefitter | 73.32 |
| 50 mm branch | 72 | 0.30 | Steel pipe DN50 | 21.60 | Pipefitter | 426.89 |
| 25 mm drop | 7.2 | 0.35 | Steel pipe, any size | 2.52 | Pipefitter | 49.80 |
| 150 mm riser | 4 | 0.62 | Steel pipe DN150 | 2.48 | Pipefitter | 49.01 |
| Reducer | 1 | 0.25 | Fitting, any | 0.25 | Pipefitter | 4.94 |
| Tee 150 × 50 | 3 | 0.25 | Fitting, any | 0.75 | Pipefitter | 14.82 |
| Tee 100 × 50 | 3 | 0.25 | Fitting, any | 0.75 | Pipefitter | 14.82 |
| Gate valve 150 | 1 | 3.50 | Gate valve assembly DN150 | 3.50 | Pipefitter | 69.17 |
| Check valve; hangers | | | No entry | none | | none |
| **Total** | | | | **50.95** | | **1,010.21** |

A line with no price still has labour (the tees); a line with no entry has no hours and is
listed, not guessed.

**The cost build-up.**

| Component | Amount | How |
| --- | ---: | --- |
| Materials | 3,800.33 | Heads and pipework lines |
| Fittings | 64.00 | The reducer; the tees have no price |
| Valves | 890.00 | The gate valve; the check valve has no price |
| Equipment | 0.00 | |
| Wastage | 141.12 | 5% of each pipe line's quantity at its rate (B6) |
| Labour | 1,010.21 | Calculated, above |
| Ten other components | not set | Nobody has entered them; they add nothing |
| **Direct cost = cost** | **5,905.66** | |
| Margin | 472.45 | 8% of cost, entered by the Senior Estimator |
| **Total, excluding GST** | **6,378.11** | |
| GST at 9% | 574.03 | The rate in force on 4 October 2026 |
| **Total, including GST** | **6,952.14** | |

- **Validation rules:** each amount is quantity × rate, rounded half up to the cent; the
  build-up's lines add to its total; GST is shown beside the total and never inside it; no
  price exists without a rate entry; "not set" is not zero.
- **Expected exceptions:** three rate warnings (one expired, all three ending before the
  tender validity); six lines without a price; three lines without labour hours.

### Stage 11 · Clarifications, risks and qualifications

**Clarification candidates: 8.** The six issues of stage 8 and the two flagged variances of
stage 9 (B4, the drops; C3, the flow switch). The two conflicts, at least, have engineering
content and need the Design Manager before issue. None is drafted into an issued clarification.

**Scope-gap checklist**, for the sprinkler system (B9):

| Item | Status | From |
| --- | --- | --- |
| Fire pumps and jockey pumps | **Open** | Nothing settles it |
| Fire water tanks | **Open** | Nothing settles it |
| Breeching inlets | **Open** | Nothing settles it |
| Hydraulic calculations | Included | 6.6 |
| Shop drawings | Included | 6.6 |
| Testing and commissioning | Included | 6.1, 6.3 |
| Authority inspections and FSC support | Included | 6.7 |
| Builder's works | Excluded | 7.3 |
| Power supply interfaces | By others | 7.1 |

Each status is a proposal: no person has decided one.

**Risks: 8.**

| Risk | Family | Evidence | Proposed treatment | Impact |
| --- | --- | --- | --- | --- |
| Design and build | Design responsibility | 8.1 | Qualify | For a person to enter |
| Shop drawings | Design responsibility | 6.6 | Price | For a person to enter |
| Hydraulic calculations | Design responsibility | 6.6, 8.2 | Price | For a person to enter |
| Engagement of a Qualified Person | Design responsibility | 8.3 | Price | For a person to enter |
| Night work | Execution | 8.4 | Price | × 1.20: 10.19 h, 202.04 |
| Occupied building | Execution | 8.5 | Price | × 1.25: 12.74 h, 252.55 |
| Shutdowns of existing systems | Execution | 8.6 | Price | Not computed: no multiplier describes it |
| Basement levels | Execution | Level B1 of FP-B1-201 | Price | × 1.10: 5.10 h, 101.02 (B10) |

An impact is the multiplier's extra on the labour it reaches: 50.95 hours and 1,010.21.
None is in the estimate, because none is confirmed or treated.

Not expected: work at height (no ceiling above 3,000 mm is stated), high-rise, congested
ceilings, restricted access.

**Qualifications.** The four default measurement conventions, worded as `boq.yaml` words
them. No qualification is proposed from a risk until a person treats one as "qualify".

### Stage 12 · Review pack and submission

| | |
| --- | ---: |
| Priced bill | 4,754.33 |
| Direct cost | 5,905.66 |
| Total, excluding GST | 6,378.11 |
| GST | 574.03 |
| Total, including GST | 6,952.14 |
| Unpriced lines | 6 |
| Open: specification issues / checklist items / untreated risks | 6 / 3 / 8 |
| Flagged bill variances / rate warnings | 2 / 3 |

Gates: **G1 approved; G2, G3 and G4 not decided.** The bid is not ready for G3: G2 is not
approved, three checklist items are open and eight risks are untreated. Nothing is frozen
or submitted.

- **Validation rules:** the pack's figures are stage 10's, to the cent; an attempt to
  approve G3 is refused and says why.

## 3. Stage-to-stage traceability

| Stage | Input | Output artifact | Used by | Key validation |
| --- | --- | --- | --- | --- |
| 1 Intake | Three files, two libraries | Documents by type | 2, 8, 9, 10 | The hidden sheet is not read |
| 2 Title blocks | Title block cells | The register; level B1 | 7, 8, 11 | One current revision |
| 3 Views and scale | View title, dimensions, notes | A verified 1:100 plan; two notes | 5, 6, 8 | The notes are kept with their sheet |
| 4 Legends | The legend | 8 rows with object types | 5 | "UP TYPE" is upright |
| 5 Detection | Symbols, stage 4's types | 27 instances | 7 | No flow switch installed |
| 6 Pipe network | Pipe lines, annotations | Length by size | 7 | Every run sized |
| 7 Takeoff | Stages 5, 6 and the rules | 9 drawn and 7 derived items | 8, 9, 11 | No unverified attribute on an item |
| 8 Specification | The clauses, stage 3's notes, stage 7's items | 29 attributes, 13 obligations, 6 issues, the scope matrix | 11 | Section 5 is not read as fire protection |
| 9 Bill | Stage 7, the template, the client's workbook | Our lines; 14 client lines mapped; 2 flagged | 10, 11 | Every item in exactly one line |
| 10 Pricing and labour | Stage 9, both libraries, the labour table | Prices, hours, the build-up | 11, 12 | Quantity × rate to the cent |
| 11 Clarifications and risks | Stages 2, 8, 9, 10 | 8 candidates, 9 checklist items, 8 risks | 12 | Each cites its source |
| 12 Review pack | Stages 9 to 11, the approvals | Figures, open items, gate states | The reviewer | The figures are stage 10's |

Every final figure traces back. For example the 549.01 in materials ← 8.05 m × 68.20
(quotation Q-2026-1001) ← "150 mm pipe (main)" ← the takeoff item "pipe, DN150, main" ←
three runs on FP-B1-201 sized by "150Ø" ← the current revision R01 of `FP-B1-201.dxf`.

## 4. Golden final output

**Expected result.** An estimate under review:

| | |
| --- | ---: |
| Sprinklers | 24 (16 pendent, 4 upright, 4 sidewall) |
| Valves; reducer | 2; 1 |
| Drawn pipe | 88.30 m (8.05 m DN150, 8.25 m DN100, 72.00 m DN50) |
| Total, excluding GST | SGD 6,378.11 |
| Total, including GST at 9% | SGD 6,952.14 |
| Lines without a price | 6 |
| Gates | G1 approved; G2 to G4 not decided |

**Calculations.** 3,800.33 + 64.00 + 890.00 + 141.12 + 1,010.21 = 5,905.66. × 8% = 472.45.
5,905.66 + 472.45 = 6,378.11. × 9% = 574.03.

**Business rules applied.** Only a person's decision moves a proposal on: unverified
specification values are not on items, an unconfirmed rate match is not a price, an
unconfirmed multiplier is not in the labour. A line with no rate is unpriced, not zero.
Quantities are net; the allowance is separate. GST is separate.

**Exceptions and risks.** The total is short of a real tender price by what is unpriced
(the check valve, six tees, hangers if they are to be priced) and by the client's 8,000 of
provisional sums (B8). The eleven ambiguities in part 11.

## 5. Comparison criteria

| Type | Fields | Rule |
| --- | --- | --- |
| A. Exact | Document types; drawing number, revision, level, status; scale and verdict; each legend row's type; every count; each client line's mapping and flag; each line's price status, rate and amount; every figure of the build-up; gate states | Equal; money to the cent |
| B. Semantic | Descriptions of legend rows, bill lines, obligations, issues, risks and clarifications | Same meaning |
| C. Tolerance | Pipe lengths within 5%; positions within 250 mm; labour hours within 2%; a labour cost within one cent; a risk's impact within 2% | As stated |
| D. Evidence | Each item names its sheet and place or its rule and version; each attribute, obligation, issue and risk its clause; each issue both sides; each price its rate entry and source; each labour line its productivity entry | The cited source is the reference's |
| E. Completeness | 8 legend rows; 27 instances; 9 drawn and 7 derived items; 29 attributes; 13 obligations; 6 issues; 8 scope rows; 14 client lines; 8 clarification candidates; 9 checklist items; 8 risks | Nothing missing; anything extra is listed for review |

## 7. Defect classification

| Class | Examples in this case |
| --- | --- |
| Critical | A total that differs; a line priced at zero or priced from the proposed tee rate without a person; a specification value on an item while it is a proposal; a multiplier in the labour that nobody confirmed; a sprinkler count other than 16, 4 and 4; GST inside the total |
| High | A conflict between clause and note not raised; clause 5.1 read as a fire protection attribute; client item C3 mapped to an item, or B4 not flagged; the expired rate shown without its warning; a risk of section 8 missed; the level not B1 |
| Medium | A risk found with no clause cited; the basement risk's impact on other hours than the reference's; a checklist status proposed from the wrong clause; the hidden sheet's cells read as lines and then discarded |
| Low | Wording; order; line numbering |
| Acceptable variation | The figures of `if_every_head_takes_a_drop` until B1 is settled (`golden.json` records them value by value as each stage's `alternatives`, so that a figure which is neither reading's is still a defect); hanger lines absent under "deemed included" (B7); two candidates on one subject grouped |

## 8. Evaluation score

The weights are the test strategy's.

| Dimension | Weight | Checks in this case |
| --- | ---: | --- |
| Data extraction accuracy | 20% | Stages 1, 2 and 4: 5 files, 1 register row, 8 legend rows |
| Intermediate work product accuracy | 20% | Stages 3, 5 and 6: 1 view, 2 notes, 27 instances, 3 lengths |
| Business rule accuracy | 20% | Stage 7: 7 derived items and the attribute rule. Stage 8: 29 attributes, 13 obligations, 6 issues, 8 scope rows. Stage 9: 14 mappings, 2 flags |
| Calculation accuracy | 15% | Stage 10: 10 amounts, 12 labour lines, 11 build-up figures. Stage 12: 5 figures |
| Evidence and traceability | 10% | Every item, clause, rate and productivity source |
| Completeness | 10% | Part 5, row E |
| Final output quality | 5% | Part 4 |

## 9. AI-specific evaluation

| Check | In this case |
| --- | --- |
| Hallucination | An obligation with no clause; an attribute from section 5; a price with no rate entry; a flow switch or a test header in the takeoff; a risk no clause or level supports; a tee priced at a rate the list does not have |
| Grounding | Each issue opens to its clause and to the note on FP-B1-201; each price to its rate entry; each labour line to its productivity entry; the drop to the rule's defaults |
| Consistency | Read twice, the same work products. Everything here is deterministic except where a model is asked to map a client line or propose a rate match, which must give the same answer or none |
| Completeness | The condition "within the basement car park" is kept with clause 2.1.3; the missing upright clause is noticed; the provisional sums are seen as sums; the expired rate is warned of |
| Instruction following | Nothing a model or a rule proposed is used as decided: specification attributes, the tee's rate, the labour multipliers, the risk treatments |
| Agent handoff | Stage 2 gives stage 8 and stage 11 the level; stage 3 gives stage 8 the notes; stage 9 gives stage 10 a group for each line, which decides its build-up component |
| Error propagation | Section 5 read as fire protection adds a false material at stage 8. A missed level loses the material conflict at stage 8 and the basement risk at stage 11. Drops for every head move stage 7 by 3.6 m, stage 9's variance to −10.0% and the total by 76.28 |

## 10. Test oracle

- **Made from:** the fixture generators' source and constants (`firebid.evals`:
  `synthetic_qto`, `synthetic_network`, `synthetic_spec`, `synthetic_boq`,
  `synthetic_rates`, `synthetic_labour`) and the configuration files named in
  `manifest.json`.
- **How each stage was made:**
  - Stages 5 and 6 are what the generator says it drew, got by running the generator. The
    platform's readers were not called.
  - Stage 8's attributes, obligations, issues, interfaces and risks, and stage 9's mapping,
    are the fixtures' own stated answers (`EXPECTED`, `EXPECTED_OBLIGATIONS`,
    `SEEDED_ISSUES`, `EXPECTED_INTERFACES`, `EXPECTED_DESIGN_RISKS`,
    `EXPECTED_WORDING_RISKS`, each line's `maps_to`).
  - Stages 7 and 9 to 12 were worked out by hand-written arithmetic from the fixtures and
    the configuration. No pricing, labour, costing, takeoff or risk function of the
    platform was called, and no bid was run. The arithmetic is kept beside this file as
    `work_out.py`, which writes `golden.json` and can be read line by line.
- **Made by:** Claude (Opus 5.5), in a session that had not seen the platform's output for
  this tender. **Two weaknesses, stated as such:**
  - To learn rules that only the code states (how a rate is matched, how the hourly labour
    cost is built and rounded, which build-up component a bill group feeds), the author
    read the platform's own modules and some of its tests. Where the reference follows the
    code's rule and not a requirement's, the platform can only agree with it. Stage 10's
    labour rates and the build-up are the clearest case, and are where a person should look
    first.
  - The fixtures' stated answers were written by the people who built the platform, for
    its tests. They are independent of any one run, not of the builders' understanding.
- **Not yet done:** verification by a person; a comparison run against the platform (the
  comparison tooling is not built). The scenario matches the bid that
  `backend/tests/db/test_submission.py` prepares, with the client's bill and the tender
  dates added, so that a run can be exported from it when the exporter exists.

## 11. Known ambiguities and assumptions

| | Ambiguity | Expected | Alternative | Impact | To resolve |
| --- | --- | --- | --- | --- | --- |
| B1 | Which heads get a drop. The rule's note says "a pendent head"; the client bills 12 m, which is 24 heads at 500 mm | Pendents only: 16 × 0.45 = 7.2 m | Every head: 24 × 0.45 = 10.8 m | 3.6 m of DN25; total 6,454.39 against 6,378.11 | Senior Estimator. The same question as TC-SYN-001's A1 |
| B2 | Whether the main is measured as drawn or through its valves and fittings. The default convention says through | As drawn: DN150 8.05 m | Through: DN150 9.05 to 9.50 m, DN100 to 8.50 m | Up to 1.45 m of DN150, about 99 before labour and margin; and the client's B1 variance becomes +13% to +19%, flagged | Senior Estimator. TC-SYN-001's A6 |
| B3 | The riser: its size is not stated, and a basement's riser is given the default 4.0 m and one level | DN150, 4.0 m | No size, raised for a person | 272.80 of pipe and 49.01 of labour | Estimator convention |
| B4 | An elbow where the riser turns into the main | None | 1 elbow DN150 | 1 fitting, unpriced, 0.25 h | Estimator convention |
| B5 | A rate that expired before the pricing date (DN100 pipe, PO-889, to 30 June 2026) | Priced at 45.60 with an "expired" warning a person must see | Not priced until a valid rate exists | 376.20, with its wastage and margin | Commercial: may an expired rate price a line |
| B6 | Which pipe takes the 5% allowance | All pipe, drops and riser included: wastage 141.12 | Mains and branches only: 123.12 | 18.00 | Product owner |
| B7 | Hangers, when the convention has them deemed included in pipework rates | Counted at takeoff and listed in the bill, unpriced | Counted at takeoff, not listed in the bill | Unpriced lines 6 or 3; no money | Product owner |
| B8 | The client's provisional sums (D1 5,000 and D2 3,000) | Shown against the client's bill; not in our total until a person carries them | Carried automatically as allowances | 8,000 | Bid Manager |
| B9 | Which systems the checklist covers: the takeoff has only sprinklers; the specification also has hose reel and hydrant sections | Sprinkler only | Also hose reel and hydrant, adding their items as open | Up to 18 more checklist items | Product owner |
| B10 | Which labour a level's risk reaches when the bill rolls pipework up over the building | All of it, as B1 is the only level: 101.02 | Only the lines the bill keeps by level (the heads): 20.88 | 80.14 of proposed allowance; nothing in the total | Product owner |
| B11 | A rate entry that says more than the line (the tee's brand) | Proposed for a person; not priced | Priced | 125.40 | Stated in the rate list's own notes; listed because it is the rule most easily relaxed |

Assumptions: the one sheet is the whole tender's drawings; the riser serves one level; the
specification's revision is the one printed inside it, Rev B; the organisation has no other
rate or productivity entries.
