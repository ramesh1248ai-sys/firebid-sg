# Open items: analysis and plan · 2026-10-08

What is open after PR #67, where each item comes from, how to fix it, and in what order.
Sources: `docs/reports/phase2-gaps.yaml`, the build log, and the measurements on the real
bid (MOH set, 148 sheets, 12,088 candidate symbols).

Sizes are relative: **S** is one focused change with its tests, **M** touches several
modules or needs a measurement loop on the real bid, **L** needs a design step first.

## 1. Summary

There are 22 engineering items in four groups, and 13 decisions that belong to other people
(section 8):

| Group | Items | Blocked on a decision? |
| --- | --- | --- |
| A. Symbol matching on real drawings | 6 | One is a decision only; the rest can start |
| B. Reading pipeline | 4 | No |
| C. Views, scale and grid | 4 | One is |
| D. Engineering hygiene and pilot readiness | 8 | Three are |

The recommended order is in section 7. In short: first the fixes that need no decision and
make every later measurement cheaper (stale proposals, "Read again" for symbols, one
matching pass per upload, CI on docs), then the lettered devices, then a first real count
on one floor, which is the first evidence that the counts can be trusted.

Three entries in the gap list are out of date and should be closed (section 6).

**Status, 2026-10-08:** step 1 of section 7 is done (A2, B1, B2, D1 and the gap-list
clean-up); see the build log entry "Open items, phase 1". B2 was done by skipping the pass
when no sheet was read, not by a separate job, because detection reads the matches and must
run after them.

Step 2 is done except for naming groups on the real bid, which is a person's act (build
log entry "Open items, phase 2"). A1 step 1 recovered 417 lettered devices. A5 records and
shows what was set aside; its per-bid switch waits for the rules to be confirmed. The A3
measurement ruled out joining shapes by distance: there is no gap that separates a device's
pieces from its neighbours. A4 found that the workbench shows no picture of an unnamed
group, which has to be fixed before 578 groups can be named. "Read again" was run on the
real bid through the job: 148 sheets in 17 min 45 s, none failed.

## 2. Group A: symbol matching on real drawings

State on the real bid today: 2,975 candidates match a legend row, 1,172 match only a stale
proposal, 7,941 match nothing (626 groups). No row is confirmed, so nothing is counted.

### A1. Lettered devices drawn to other proportions (M)

- **Problem.** About 630 boxes carry a legend row's letters and match no row: FI 172,
  SAP 101, 2SFH 190, FS 203.
- **Cause.** The plans draw the box to fit: the legend's FI is 11 × 5.4 mm lying down, the
  plan's is 3.5 × 6 mm standing up. Shape distance to the right row, measured:

  | Letters | Count | Median | Range |
  | --- | --- | --- | --- |
  | FI | 172 | 0.130 | 0.118 to 0.165 |
  | SAP | 101 | 0.162 | 0.154 to 0.173 |
  | 2SFH | 190 | 0.162 | 0.070 to 0.475 |
  | FS | 203 | 0.298 | 0.121 to 0.423 |

  The tolerance is 0.07.
- **Fix, step 1.** In `drawings/symbols.Candidates.gaps`, when both signatures carry the
  same letters and the letters are two characters or more, hold the pair to a looser
  tolerance (`LETTERED_TOLERANCE`, proposed 0.20). Report such a match as `how="letters"`
  so the Symbols page can show it as weaker than a shape match. One-letter symbols (S, H)
  keep the strict tolerance: one letter in another outline is too easy to meet.
- **Expected.** FI and SAP recovered in full, about two thirds of 2SFH, few FS.
- **Fix, step 2, only if FS matters.** FS sits inside a valve assembly, so its cluster is
  the assembly, not the box. Find these from the letters instead: for each legend row with
  two or more letters, each text on a plan equal to those letters and enclosed by a closed
  shape is one instance. This is a second detector and needs its own tests.
- **Tests.** Unit: same letters, different proportions match; one letter does not; other
  letters still never match. Re-read the real bid and tabulate each row by letters.
- **Decision.** The Senior Estimator should confirm that a box with a row's letters is
  that row's device. It does not block the work: nothing is counted until the row is
  confirmed, and the weaker matches are labelled.

### A2. Stale proposals still claim symbols (S)

- **Problem.** Seven proposals that no legend row points to claim 1,172 symbols by shape
  alone. They were left by earlier readings of the same legends.
- **Fix.** In `services/symbols.match_instances`, use a proposed mapping only if some
  legend entry (on any bid of the organisation) points to its lineage. Confirmed mappings,
  including "unlisted symbol" ones, are untouched. No data is changed, so it is reversible.
- **Later, if wanted.** A `withdrawn` state written when a bid is read again, so the
  library page stops listing them.
- **Tests.** DB test: an orphan proposal claims nothing; a proposal with a row still does;
  a confirmed mapping with no row still does.

### A3. Devices drawn as pieces that do not touch (L)

- **Problem.** A candidate is one group of touching shapes in one pen. A bell, a sounder
  with rays, or a call point with its attachments is several groups, so the device matches
  by one piece or not at all. The legend rows show it: BELL's sample is 1.3 × 1.2 mm and
  STROBE LIGHT (WALL)'s is 1.6 × 0.8 mm, which are pieces, not devices.
- **Design step first.** Measure on the real bid how many legend rows are a piece of their
  cell, and how far apart a device's pieces are compared with the gap between neighbouring
  devices. The fix is only safe if the first is clearly smaller than the second.
- **Likely fix.** (1) A legend row's sample becomes every shape in its symbol cell, not the
  largest group. (2) On plans, groups in one pen within a small gap are also offered as one
  candidate when their union is symbol-sized; the union is kept only where it matches a
  legend row and its pieces do not.
- **Risk.** Joining too eagerly merges a device with the pipe fitting beside it. This is
  why the union should be legend-guided, not unconditional.

### A4. Shapes no legend explains (operational, S for tooling)

- **Problem.** The largest unexplained groups are pipe fittings, valve assemblies and one
  empty box on every sheet (1,585).
- **Fix.** No detector change. Name the large groups once with "unlisted symbol" (the empty
  box as "Not an installed object"); the mapping is remembered for the consultant. Check
  that the Symbols page shows a crop for an unexplained group so this is practical for 626
  groups; if not, add it.
- **Decision.** Senior Estimator: are fittings counted from symbols, or derived by rule?

### A5. Nothing records what was set aside (S to M)

- **Problem.** Straight strokes, leaders and the screened base plan are dropped silently.
  A tender that prints its services in grey would lose its symbols without a word.
- **Fix.** `candidates()` returns counts of what each rule set aside; store them per sheet
  and show them on the sheet ("3,012 shapes set aside: 2,700 base plan, 240 strokes,
  72 leaders"). Add a per-bid switch "services are printed in grey" that turns the base
  plan rule off.
- **Decision.** Product owner and Senior Estimator confirm the three rules.

### A6. The ticked circle (decision only)

1,812 circles with four ticks match "STROBE LIGHT (CEILING)", drawn the same way in the
legend. That is over three times the 536 smoke detectors. The Senior Estimator should say
what they are before the row is confirmed. No code change is planned.

## 3. Group B: reading pipeline

### B1. "Read again" does not re-read symbols (M)

- **Problem.** After a detector change, a bid keeps its old candidates. The real bid has
  been re-read five times by a script on the database.
- **Fix.** Extend `parse_pipeline.to_read_again` to find sheets whose symbols were read by
  an older `services/symbols.DETECTOR_VERSION`. The version must be stored per sheet (on
  the sheet's geometry record), because a sheet with no instances has no row to carry it.
  One `symbols.again` job per bid in the parser pool reads a sheet at a time from stored
  geometry, then matches once. The script used so far is the prototype; it took about
  15 minutes for 148 sheets.
- **Tests.** As `TestReadAgain`: older version is found, read once, and not found again.

### B2. A matching pass per document (S)

- **Problem.** Every finished document matches every instance of the bid. A pass is 3 to
  4 seconds now, but a tender of 148 one-sheet files still does 148 passes.
- **Fix.** Queue matching as its own job with one lock per bid, so documents that finish
  together share one pass. This removes nearly all of the cost with a small change.
- **Not planned now.** True incremental matching with a stored shape digest. Worth doing
  only if a pass grows again; the digest column would be the first step.

### B3. Memory on the heaviest sheet (watch)

Finding symbols on a two-million-primitive sheet takes 1.2 GB, inside the job's 2 GiB. No
work unless a heavier sheet is met.

### B4. The design basis across the whole set (M)

The set has been read; its design basis and match lines have not been tallied beyond the
four tenth-storey sheets. Run the design-basis read over the set and record the result.
This belongs with the real count in section 7, step 3.

## 4. Group C: views, scale and grid

| Item | Problem | Fix | Size |
| --- | --- | --- | --- |
| C1. Six plans need a calibration | No dimension and fewer than three known gridlines | Operational: calibrate on a known length in the pilot runbook. Sheet A03-B6-04 needs a look first; I have not re-examined why its calibration is wrong | S |
| C2. A03-B2-05 has no grid | Its lettered lines run three ways; the detector assumes two | Let a grid block have more than two line families. One sheet, so low priority | S to M |
| C3. No grid index on real sheets | Detections on two sheets cannot be matched by grid (FR-VIS-08) | Needs the decision below, then give each labelled block its own index | M |
| C4. A schematic view beside the plan on 12 sheets | The helipad and upper-roof sheets get a second, false view | Investigate what the view classifier takes for a schematic there, then tighten it | M |

Decision for C3: the product owner and Senior Estimator say whether a real sheet's grid may
give an index now that each block's lines are told apart by their labels.

## 5. Group D: engineering hygiene and pilot readiness

| Item | Fix | Size |
| --- | --- | --- |
| D1. CI runs in full on docs-only changes, twice | `paths-ignore` for `docs/**` and `*.md`; `push` only on `main`. Keep the requirement-coverage check running when `docs/requirements` changes | S |
| D2. End-to-end tests skip the Phase 2 pages | One test that takes a bid from pricing to G4; walk Costing, Labour, Clarifications, Risk, Review and Design in a browser | M to L |
| D3. User manual | Have a workbench and BOQ user read sections 8 and 9; retake screenshots from a bid with a specification, a priced BOQ and clarifications | S |
| D4. Priced client workbook is not in the frozen submission | Add it to the snapshot before the pilot's first submission | S to M |
| D5. Submission snapshots have no object lock without a bucket | Configure the locked bucket in every deployed environment and exercise it | S, operational |
| D6. Outcome free text is copied into the audit log | Decide with the DPO; say on the page not to name individuals | S after the decision |
| D7. Accepted risk allowances sit beside the estimate, not in it | Product owner decides; then add a build-up component | M after the decision |
| D8. DXF export is 110 MB | Product owner accepts, or the layout alone is offered | S after the decision |

## 6. Gap-list entries to close

These read as open in `phase2-gaps.yaml` but I believe they are done. Each needs a quick
check before it is removed:

- **"CI has not run since P2-06".** CI has run on PRs #63 to #67. Confirm it has run on
  `main` and close.
- **"semgrep, gitleaks and trivy have not run on the Phase 2 code".** The Security scans
  job passes on every PR. Confirm it runs those three and close.
- **"Four sheets failed for memory ... and were not read again".** The last re-read
  reported 148 of 148 sheets. Confirm all four are among them and reword the entry to what
  is left (the design basis for the set).

## 7. Recommended order

1. **No decision needed, makes everything after it cheaper.**
   - A2 stale proposals (S)
   - B1 "Read again" for symbols (M)
   - B2 one matching pass per upload (S)
   - D1 CI on docs (S)
   - Section 6 gap-list clean-up (S)
2. **Symbol matching.**
   - A1 lettered devices, step 1 (M)
   - A5 record what was set aside (S to M)
   - A4 name the large unexplained groups on the real bid (operational)
   - A3 design step for split devices (measure first; build only if the gap is clear)
3. **First real count on one floor.** Level 10 is the natural choice: its four sheets are
   the ones match lines were proved on, and the estimators' head count for sheet 1 (346) is
   already on record. Needs the estimators' device counts for that floor and the fire
   alarm legend rows confirmed. Compare by row, and record the result as evidence. This is
   what tells us whether A3 and A1 step 2 are worth building.
4. **Views and grid.** C4 schematic view, C1 calibration of A03-B6-04, then C3 and C2.
5. **Pilot readiness.** D2 end-to-end test, D4 workbook in the snapshot, D3 manual, D5.

## 8. Decisions needed, and from whom

| Decision | Who | Blocks |
| --- | --- | --- |
| What the ticked circle is | Senior Estimator | Confirming the strobe row |
| A box with a row's letters is that row's device | Senior Estimator | Nothing; confirms A1 |
| Whether fittings are counted from symbols | Senior Estimator | A4, and whether A3 is worth it |
| The three candidate rules | Product owner, Senior Estimator | Nothing; confirms A5 |
| Whether a real grid may give an index | Product owner, Senior Estimator | C3 |
| Which floor, and its device counts | Estimating team | Step 3 |
| D2: provider data terms and hosting | Sponsor | Any model route on a real provider, cost per tender |
| D3: historical tenders for a Phase 2 golden set | Sponsor, Estimating Manager | Phase 2 accuracy evidence |
| D5: named approvers for the gates | Sponsor | Nothing technical |
| Placeholder figures in the seven config files | Estimating Manager, Commercial Director | The pilot |
| Turnaround baseline in `config/kpi.yaml` | Estimating Manager | The turnaround exit criterion |
| Risk allowance in the build-up; DXF size; audit free text | Product owner, DPO | D6 to D8 |
| Accept the Phase 2 systems baseline under their own name | Product owner | The gate review |

## 9. What this plan does not cover

- The assisted-mode pilot itself (three live tenders), which is the only way the three
  Phase 2 exit criteria can be measured.
- Route-backed `compare-models` and the load test on priced bids: both wait on D2 and on
  the pilot showing typical sizes.
- The review of `scripts/generate_kt_presentation.py`, which is yours.
