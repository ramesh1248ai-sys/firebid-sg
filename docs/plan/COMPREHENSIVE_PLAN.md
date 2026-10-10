# Comprehensive plan · 2026-10-11

Everything that is open on FireBid SG after PR #88, in one place: what it is, why it
matters, how to do it, who it waits on, and in what order. It takes over the ordering from
`OPEN_ITEMS_PLAN.md` (8 October), whose analysis of each symbol-matching and view item still
stands and is referred to here by its item letters (A1 to D8).

Sources: `docs/reports/phase2-gaps.yaml`, `OPEN_ITEMS_PLAN.md`, `TEST_STRATEGY.md` section
11, the build log to 10 October, and what was found while writing the user manual.

Sizes are relative: **S** is one focused change with its tests, **M** touches several
modules or needs a measurement loop on the real bid, **L** needs a design step first.

## 1. The goal

**Pass the Phase 2 gate.** Its three criteria are measures of live tenders: estimate
turnaround down 30%, no unsourced price, and at least 70% of clarifications issued with
only minor edits. None can be judged until an assisted-mode pilot has run on at least three
live tenders against a recorded baseline. So the plan is ordered by one question: what
stands between today and a pilot whose numbers can be believed?

Three things do:

1. **A bid can be run end to end by its own people.** True since PR #88 for the synthetic
   tender; a few gaps remain for a real one (section 3, stream P).
2. **The counts on a real tender can be trusted.** Not yet shown: no legend row of the real
   set is confirmed, and no real count has been compared with the estimators' (stream R).
3. **The business inputs are real.** Rates, wages, thresholds and the turnaround baseline
   are placeholders, and the provider decision is open (section 5).

## 2. Where things stand

| Area | State |
| --- | --- |
| Phase 2 build (P2-01 to P2-09) | Done. Every Phase 2 requirement has a test. Exit report: not ready for the gate, ready for the pilot |
| Real tender (MOH, 148 sheets) | Read without error. 2,975 candidates match a legend row; 8,693 match none, in 578 groups. Nothing is confirmed, so nothing is counted |
| Golden reference comparison | Built for exact, tolerance, completeness and evidence on stages 1 to 12, with levels, attributes, systems and instance positions. Three synthetic packages run in CI. Semantic comparison is not built |
| Screens | Every step of a bid has a screen since PR #88: team, the bid's moves, G2, productivity import |
| User manual | Walkthrough, exceptions guide and screen reference, with a Word copy generated from them |
| CI | Green on `main`. Docs-only changes skip the long jobs |
| Model routes | None has run against a real provider: decision D2 is open |

## 3. Work, by stream

### P. Pilot readiness: a real bid run by its own people

| ID | Item | How | Size | Waits on |
| --- | --- | --- | --- | --- |
| P1 | The priced client workbook is not in the frozen submission (was D4) | Add it to the snapshot's files and manifest; test that it verifies and downloads | S to M | Nothing |
| P2 | No end-to-end test goes past the workbench (was D2) | One Playwright test on a fresh stack: team, moves, upload, G1, BOQ, pricing, labour, margin, risk, G2 to G4, outcome. The script that made the manual's screenshots is the prototype | M | Nothing |
| P3 | Nobody can be taken off a bid, or have their role changed | `DELETE` and `PATCH` on a member, kept to the bid manager; refuse removing the last bid manager; audit both. Buttons on the Team panel | S to M | Nothing |
| P4 | A productivity figure cannot be entered by hand on screen | A form on the productivity library for the API's existing single-entry route, with the source required | S | Nothing |
| P5 | Accepted risk allowances sit beside the estimate, not in it (was D7) | A build-up component fed by accepted allowances | M | Product owner |
| P6 | Outcome free text is copied into the audit log (was D6) | Say on the page not to name individuals; hold or drop the text in the audit event as decided | S | DPO |
| P7 | Submission snapshots have no object lock without a bucket (was D5) | Configure the locked bucket in each deployed environment and exercise the lock | S, operational | Hosting (D2) |
| P8 | The pilot runbook | Calibrate the six unproved plans on a known length; open the bid on the day the tender arrives; name the large unexplained symbol groups once; who approves each gate | S | R1 for the naming step |
| P9 | The manual does not walk through addenda, quotations, design development or the folder upload | Stage each on the local stack and add it to the walkthrough or the exceptions guide | M | Nothing |
| P10 | Placeholder figures in seven config files | The Estimating Manager and Commercial Director confirm each; no code | Business | Section 5 |

### R. Real-tender accuracy: the first count that can be believed

| ID | Item | How | Size | Waits on |
| --- | --- | --- | --- | --- |
| R1 | An unnamed symbol group is shown as a count, with no picture (found in A4) | Show a crop of one instance, and where it is, in "Symbols nobody has named". Without it 578 groups cannot be named | S to M | Nothing |
| R2 | Name the large unexplained groups on the real bid | A person's act in the workbench, once per consultant: the empty box as "Not an installed object", fittings as decided | Operational | R1; the fittings decision |
| R3 | The design basis and match lines across the whole set (B4) | Run the design-basis read over the 148 sheets and record it; a second consultant's drawings when one is available | M | Nothing |
| R4 | **First real count on one floor.** Level 10 of the MOH set | Estimators give the device counts; the fire alarm rows are confirmed; compare by row; file it as golden package TC-MOH-L10 (kept out of git, guardrail 8) | M | Estimators' counts; the ticked-circle decision |
| R5 | Devices drawn as pieces that do not touch (A3) | Only if R4 shows them missed: a legend row's sample becomes everything in its cell. Not by distance: measured, there is no gap that separates a device from its neighbours | L | R4 |
| R6 | Lettered devices inside an assembly: about 200 flow switches (A1 step 2) | Only if they are to be counted from symbols: find a row's letters on the plan and take the closed shape around them | M | R4; Senior Estimator |
| R7 | Views and grid (C4, C1, C3, C2) | The false schematic view on 12 sheets; the calibration of A03-B6-04; a grid index on real sheets; the sheet with three line families | S to M each | C3 waits on a decision |
| R8 | A stale proposal is still listed in the library | Withdraw it when its bid is read again, if so decided | S | Product owner |

### E. Evaluation: the golden reference comparison

| ID | Item | How | Size | Waits on |
| --- | --- | --- | --- | --- |
| E1 | The fire pumps' driver on TC-SYN-003 | Either read the driver from the schedule's description ("ELECTRIC FIRE PUMP"), or drop it from the package | S | Senior Estimator |
| E2 | Attributes are compared by kind, not pump by pump | Put the item's tag in the takeoff export; compare tagged items one to one | S to M | Nothing |
| E3 | A defect is not linked to the earlier one that caused it | Trace a value's inputs back a stage; mark the later differences as following from the first and count them once (strategy, section 7) | M to L | Nothing |
| E4 | Semantic comparison (type B), including evidence written as prose | First the rubric route: a named reviewer's verdict recorded in the package. The model judge after D2, with its verdicts sampled by a person | M, then M | The judge waits on D2 |
| E5 | No synthetic case takes equipment past stage 7 | Fixtures for the pump room: a specification, client BOQ, rate list and productivity list. Then TC-SYN-003 to stage 12 | L | Nothing |
| E6 | Small gaps | A clarification candidate's sheet in the export; pipe runs placed as objects are; a "not specified" rule for TC-SYN-001; the breeching inlet's level; the three listed TC-SYN-002 differences each settled as the platform's, the package's or the test's | S each | Two need a person |
| E7 | No package is verified by a person | The Senior Estimator settles each package's ambiguities (six, eleven and eight) and signs it | Business | Estimating team |
| E8 | The golden run is not a gate | Once a real package is filed: run before each gate and nightly, and fail on a critical or unreviewed high defect (strategy, section 8) | S | R4; product owner on the rule |

### M. Models and cost

All of it waits on decision D2 (provider data terms and hosting).

| ID | Item | How | Size |
| --- | --- | --- | --- |
| M1 | No model route has run against a real provider | Configure the key; run the live tests; run each route on the golden set | M |
| M2 | `compare-models` scores a stand-in | Give it a route-backed predictor | S |
| M3 | AI cost per tender is unmeasured | Measure by route on the pilot's tenders; set budgets | S |
| M4 | The load test opens Phase 2 pages on unpriced bids | Price and draft on each bid, at the sizes the pilot shows | M |

### H. Hygiene

| ID | Item | How | Size |
| --- | --- | --- | --- |
| H1 | The local database holds four screenshot bids; one end-to-end test fails on this machine because of them | Remove them, or recreate the local volume | S |
| H2 | `scripts/generate_kt_presentation.py` is untracked and fails the type check | Annotate it before it is committed, or keep it out of the repository | S, yours |
| H3 | The Word manual is untracked, and its contents page needs refreshing | Decide whether a generated 6 MB file belongs in git; press F9 in Word | S, yours |
| H4 | The frontend has a linter and no formatter | Decide whether to adopt one; if so, format once in its own change so that later diffs stay small | S |
| H5 | A local stack rebuilt many times lists a person once per rebuild | Done for the team picker (newest identity per username). The underlying rows are only on development stacks | None |

## 4. Order

Each stage can start when the one before it is under way, not finished; what a stage waits
on is named.

1. **Now, no decision needed.** P1, P2, P3, R1, E2, H1. These make a real bid runnable and
   every later check cheaper. P2 is the most valuable: it turns the manual's walkthrough
   into a test that runs on every pull request.
2. **Real count, as soon as the estimators' figures arrive.** R2, R3, then R4. This is the
   first evidence that counts can be trusted, and it decides whether R5 and R6 are built.
3. **Pilot readiness, alongside 2.** P8, P9, P4, then P5 to P7 as their decisions come.
   P10 and the turnaround baseline must be in before the first live tender.
4. **Evaluation depth, in the gaps.** E1, E6, E3, E5, and E4's rubric route. E7 whenever
   the Senior Estimator has an hour for a package.
5. **The pilot.** Three live tenders in assisted mode. Then `make exit-report-p2 LIVE=1`
   and the gate review.
6. **After D2.** M1 to M4, E4's model judge, P7.

R5, R6 and R7 are built only if stage 2 shows they are needed.

## 5. Decisions, and from whom

| Decision | Who | Blocks |
| --- | --- | --- |
| D2: provider data terms and hosting | Sponsor | Stream M, the model judge, the locked bucket |
| D3: historical tenders for a Phase 2 golden set | Sponsor, Estimating Manager | Phase 2 accuracy evidence beyond synthetic tenders |
| D5: named approvers for the gates, or the role is enough | Sponsor | Nothing technical |
| The turnaround baseline in `config/kpi.yaml` | Estimating Manager | The turnaround criterion |
| Placeholder figures in seven config files | Estimating Manager, Commercial Director | The pilot |
| Which floor, and its device counts | Estimating team | R4 |
| What the ticked circle is | Senior Estimator | Confirming the strobe row; R4 |
| Whether fittings are counted from symbols | Senior Estimator | R2, and whether R5 is worth building |
| A box with a row's letters is that row's device | Senior Estimator | Nothing; confirms A1 |
| The three candidate-symbol rules | Product owner, Senior Estimator | Nothing; confirms A5 |
| Whether a real sheet's grid may give an index | Product owner, Senior Estimator | Part of R7 |
| Whether the platform reads a pump's driver from its description | Senior Estimator | E1 |
| Risk allowance in the build-up; DXF export size; a stale proposal withdrawn | Product owner | P5, R8 |
| Outcome free text in the audit log | DPO | P6 |
| The gate rule for golden packages; accept the Phase 2 systems baseline | Product owner | E8 |
| Commit the Word manual and the presentation script | You | H2, H3 |

## 6. Risks

| Risk | What would show it | What to do |
| --- | --- | --- |
| The real count is far from the estimators' | R4 | That is what R4 is for. R5 and R6 are the prepared responses; a floor counted by hand with the workbench's manual tools is the fallback for the pilot |
| The pilot starts on placeholder figures | A live estimate priced from synthetic rates | P10 is a condition of starting, not a task to finish during it |
| Decisions arrive late | Stage 2 or 3 idle | Everything in stage 1 and most of stage 4 needs none; keep those moving |
| The golden packages are trusted before a person has verified them | A defect dismissed as "the package's" | E7. Until then every package says "draft" on its report |
| A tender prints its services in grey | Symbols set aside as base plan | The per-sheet count of what was set aside is on the Symbols page; a per-bid switch waits on the rules being confirmed |

## 7. What this plan does not cover

- Phase 3 (coordination, compliance retrieval) and Phase 4 (post-award): see
  `IMPLEMENTATION_PLAN.md`. Neither starts before the Phase 2 gate.
- The conduct of the pilot itself: tender selection, training and the champions are in the
  business readiness track of `IMPLEMENTATION_PLAN.md`, section 6.
