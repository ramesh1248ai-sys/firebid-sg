# FireBid SG test strategy · 2026-10-08

How the platform is tested, and how its AI work is judged against an independent reference.
This version adds the **Golden Reference Work Product Package**: for each test tender, the
expected result of every processing stage, not only the final quantities.

Related: `eval/README.md` (the evaluation harness), `docs/decisions/D3-golden-dataset.md`
(the golden set and its owner), `eval/templates/golden_reference_prompt.md` (the brief for
producing one package).

## 1. What changes in this version

Until now the golden set held one thing per tender: the verified takeoff (counts and pipe
lengths by sheet) and the client BOQ. That answers "is the final number right?" It cannot
answer "where did it go wrong?".

On the first real tender the wrong answers came from the middle of the pipeline: a legend
row's sample read as a plain circle, a letter's backing box taken into a shape, a proposal
left by an earlier reading. Each was found by hand, weeks after the stage that caused it
was built.

So the reference now has a result for **each stage**, with its evidence, and the
comparison names the first stage that differs. Three rules come with it:

1. **The reference is independent.** It is made from the tender documents alone, never
   from the platform's output, and is not adjusted to match the platform.
2. **Every expected value carries its evidence**: document, sheet, view or grid reference,
   legend row, specification clause.
3. **A difference is classified**, not just counted: critical, high, medium, low, or an
   acceptable variation.

## 2. Test layers

The package is one layer of six. The other five exist today and are unchanged.

| Layer | What it proves | Tool | Runs |
| --- | --- | --- | --- |
| Unit and property tests | Geometry, units, money and matching rules are right in isolation | pytest, Hypothesis, Vitest | Every pull request |
| Database tests | Services, jobs and row-level security on a real PostgreSQL | pytest, Testcontainers | Every pull request |
| Integration and end-to-end | The running stack: sign-in, upload, workbench to G1 | pytest, Playwright | Every pull request |
| Synthetic evaluation suites | No regression on generated tenders whose truth is known by construction | `firebid-eval`, `make eval-gate` | Every pull request |
| Non-functional | Throughput, load, security scans, restore drill | `make pipeline-benchmark`, `load-test`, `security` | Per phase, and in CI for scans |
| **Golden reference packages** | **Each stage's work product on real and curated tenders** | **`firebid-eval` (to be extended, section 9)** | **Before each gate; nightly once filed** |

Every test that verifies a requirement carries its ID (`@pytest.mark.req`, `// req:`), and
`make req-coverage` reports which IDs have tests. That does not change.

## 3. The stages and their work products

FireBid SG takes a tender through twelve stages. A package holds the expected work product
of each stage the test case exercises.

| # | Stage | Golden work product | Compared by | If wrong |
| --- | --- | --- | --- | --- |
| 1 | Document intake | Each file: kind, sheet count, accepted or refused and why | Exact | High |
| 2 | Title blocks and registers | Each sheet: drawing number, title, revision, date, level, which revision is current | Exact | High |
| 3 | Views and scale | Each sheet: its views, stated scale, whether the scale is proved and by what, not-to-scale flag | Exact for scale and verdict; tolerance for extents | High |
| 4 | Legends and symbols | Each legend row: description, letters in the symbol, the object type it is | Exact for type and letters; semantic for description | Critical when a counted type is wrong |
| 5 | Object detection | Each sheet: count by object type, with locations for a sample | Tolerance on counts; evidence on locations | Critical for sprinklers and equipment |
| 6 | Pipe network | Each sheet: length by nominal diameter; what each run connects | Tolerance | High |
| 7 | Takeoff | Bid totals by item after duplicates between sheets are settled; rule-derived items (drops, fittings, hangers) | Tolerance; completeness | Critical |
| 8 | Specification | Obligations with their clause; issues against the drawings; the scope matrix | Semantic; evidence; completeness | High |
| 9 | Bill of quantities | Our bill; each client BOQ line mapped; variances flagged | Exact for mapping; tolerance for quantity | High |
| 10 | Pricing and labour | Each line's rate and its source; labour hours; the cost build-up | Exact arithmetic; evidence for the source | Critical |
| 11 | Clarifications, risks, qualifications | The issues that must be raised; the risks that must be found; each linked to its source | Completeness; semantic | Medium to high |
| 12 | Review pack and submission | Totals with and without GST; gate states; the frozen submission's contents | Exact | Critical |

Stage boundaries follow the platform's own handoffs, so a stage's work product is what the
next stage reads. That is what makes error propagation visible: a wrong object type at
stage 4 predicts the wrong counts at stage 5 and the wrong total at stage 7.

## 4. The Golden Reference Work Product Package

One package per test case. It has the eleven parts of the brief:

1. Test case summary
2. Expected intermediate work products, one per stage (purpose, input used, expected
   processing as short auditable logic, the work product, evidence, validation rules,
   expected exceptions)
3. Stage-to-stage traceability matrix
4. Golden final output
5. Comparison criteria
6. Machine-readable golden dataset
7. Defect classification
8. Evaluation score
9. AI-specific evaluation criteria
10. Test oracle statement (who made it, from what, and what they were not shown)
11. Known ambiguities and assumptions

### Where it is kept

```
eval/golden/synthetic/<TC_ID>/     committed
eval/golden/real/<TC_ID>/          ignored by git; kept in the eval bucket
  package.md        parts 1 to 5 and 7 to 11, for people
  golden.json       part 6, for the comparison
  manifest.json     input files with checksums; the data owner; who verified and when
```

A package made from a **real tender** holds client information. Under guardrail 8 it goes
to the eval bucket, not to git: `eval/golden/real/` is in `.gitignore`. A package made from
a **synthetic tender** is committed.

### The machine-readable dataset

`golden.json` follows the structure in the brief: one entry per stage with
`comparison_type`, `expected_output`, `mandatory_fields`, `allowed_variations`,
`tolerance` and `minimum_confidence`, then `final_output`. Field names inside
`expected_output` are the platform's own (object type keys such as `sprinkler_pendent`,
drawing numbers as printed), so no mapping layer is needed.

## 5. Who makes the reference, and how it stays independent

| Rule | Why |
| --- | --- |
| Made from the tender documents and the business rules only | A reference built from the platform's output can only agree with it |
| The author is not shown the platform's result for that tender until the package is signed | The same reason, for people |
| An AI-drafted package is a draft until an estimator verifies it | D3's meaning of "verified" stands: a figure the estimator would defend in a handover |
| An AI session that drafts a package is a fresh one, given only the inputs and the brief | A session that built or debugged the platform has seen its outputs and is not independent |
| A disagreement is settled against the source document, and the outcome recorded | Sometimes the platform is right and the reference wrong; the reference is then corrected with a reason, never silently |
| An ambiguity is written down with both readings, its impact and what would settle it | A hidden guess is scored as truth |

The brief in `eval/templates/golden_reference_prompt.md` is how a draft is produced. It is
the Golden Reference prompt with FireBid SG's description, stages, rules and standards
filled in; only the test case fields are left to complete.

**A limit to state plainly:** counting symbols on an A0 drawing is the hard part of this
domain. An AI draft of stages 5 to 7 on a real sheet is not a reference until a person has
counted. For those stages the estimator workbook (`make golden-template`) remains the
source, and the package cites it.

## 6. How outputs are compared

| Type | Applies to | Rule |
| --- | --- | --- |
| A. Exact | Drawing numbers, revisions, current-revision flags, object type of a legend row, BOQ line mapping, prices and totals, gate states | Equal after normalising case and spacing |
| B. Semantic | Legend descriptions, obligations, clarification wording, risk descriptions | Same meaning; judged by a rubric with a named reviewer, or a model judge whose verdicts are sampled by a person |
| C. Tolerance | Counts, pipe lengths, view extents, labour hours | Within the tolerance in the table below |
| D. Evidence | Every counted item, every price, every obligation and issue | The cited sheet, location, clause or rate source is the reference's, or one the reviewer accepts as equivalent |
| E. Completeness | Legend rows, object types present, issues, risks, clarifications | Nothing in the reference is missing; anything extra is listed for review as a possible hallucination |

Tolerances come from the requirements' KPIs. Those marked "to confirm" are proposals.

| Measure | Tolerance | Source |
| --- | --- | --- |
| Drawing number and revision, vector title blocks | At least 95% of sheets exact | FR-DOC-02 |
| Sprinkler count, per tender | At least 98% accuracy | §14 KPIs |
| Pipe length by diameter | Within 5% | §14 KPIs |
| Missed items | At most 5% | §14 KPIs |
| Equipment count (pumps, tanks, valve sets) | Exact | To confirm |
| Money | Exact to the cent | Deterministic arithmetic |
| Labour hours | Within 2% | To confirm |

## 7. Defect classification

| Class | Meaning | FireBid SG examples |
| --- | --- | --- |
| Critical | A wrong business decision, or an unsafe or non-compliant result | A total price differs; a legend row confirmed as the wrong object type; sprinklers under-counted beyond tolerance; a superseded revision taken off |
| High | Important data, calculation, rule or finding is wrong | Pipe length outside 5%; a sheet's scale wrong; a client BOQ line mapped to the wrong item; a specification conflict not raised |
| Medium | An intermediate result differs without changing the outcome | A view's extent differs; a symbol matched at a weak distance to the right row; a risk found but not linked to its source |
| Low | Wording, formatting or order | A description's wording; the order of the register |
| Acceptable variation | Different but equivalent, and within the rules | A clarification worded differently with the same question; a location cited by grid reference where the reference gives coordinates |

A difference is recorded against the **first stage** where the platform's output leaves the
reference. Later differences that follow from it are linked to it and not counted again.

## 8. Scoring

| Dimension | Weight | Stages that feed it |
| --- | ---: | --- |
| Data extraction accuracy | 20% | 1, 2, 4 |
| Intermediate work product accuracy | 20% | 3, 5, 6 |
| Business rule accuracy | 20% | 7 (duplicates, derived items), 8, 9 |
| Calculation accuracy | 15% | 7, 10, 12 |
| Evidence and traceability | 10% | All |
| Completeness | 10% | 4, 8, 11 |
| Final output quality | 5% | 12 |

Overall score = the weighted sum, reported with the count of defects in each class.

**The score does not override a defect.** A proposed gate rule, to be confirmed by the
product owner:

- No critical defect on any package.
- No high defect that a person's review at the gates (G1 to G4) would not catch.
- Overall score at least 90% on every package and 95% across the set.

## 9. AI-specific evaluation

| Check | How it is made |
| --- | --- |
| Hallucination | Anything in the platform's output that the reference does not have and no source supports: an object type no legend shows, an obligation with no clause, a price with no source. The database already refuses a price without a source |
| Grounding | Each counted item, obligation, issue and price has evidence that opens to the right place |
| Consistency | The same tender read twice gives the same work products. Deterministic stages must be identical; model stages must be equivalent |
| Completeness | Everything in the reference appears, in particular what is easy to miss: a legend on a plan sheet, a note that changes scope, a superseded revision |
| Instruction following | The guardrails hold: only a person confirms a mapping; nothing unconfirmed is counted; models supply labels and text, never final numbers |
| Agent handoff quality | Each stage's output has every field the next stage reads, checked against the stage table |
| Error propagation | Each difference is traced forward: which later differences it explains |

## 10. Test cases

The first packages, in the order to make them:

| Test case | Input | Reference made by | Stages | Status |
| --- | --- | --- | --- | --- |
| TC-SYN-001 | The synthetic three-sheet sprinkler tender the end-to-end test uses | The fixture generator's own truth, which is independent by construction | 1 to 7 | Drafted in `eval/golden/synthetic/TC-SYN-001/`; not verified by a person; six ambiguities to settle |
| TC-SYN-002 | The synthetic basement car park plan with its specification, client BOQ, rate list and productivity list | The generators' truth and the fixtures' stated answers; stages 7 and 9 to 12 worked out by hand from the rules | 1 to 12 | Drafted in `eval/golden/synthetic/TC-SYN-002/`; not verified by a person; eleven ambiguities to settle |
| TC-SYN-003 | The synthetic Phase 2 systems tender (pump room, floor, site plan, riser schematic) | The generators' truth | 1 to 7 | Fixtures exist; package to write |
| TC-MOH-L10 | Level 10 of the real MOH set (four sheets) | Estimators' count in the workbook; legend and register by an estimator | 1 to 7 | Needs the estimators' counts |
| TC-REAL-nn | The D3 spread: at least four consultants, three scanned sets, three with a superseded revision, three with enlarged plans | The data owner's estimators | As the tender allows | Waits on D3 |

TC-SYN-002 was first planned as the pump room tender taken through all twelve stages. The
fixtures do not allow that: the synthetic specification, client BOQ, rate list and
productivity list were all written for the sprinkler installation, and nothing bills or
prices a pump. So the case is split. TC-SYN-002 takes the sprinkler installation, as a
basement car park, through stages 1 to 12; TC-SYN-003 takes the pump room tender through
stages 1 to 7. Equipment at stages 8 to 12 has no synthetic case until fixtures are
written for it.

A case that reaches stages 8 to 12 also fixes its **scenario**: what people have and have
not decided before each stage (which proposals are verified, which rates confirmed, what
was entered). Without it the later stages have no single expected answer.

Each real package also names what it is there to catch: for TC-MOH-L10, lettered devices,
the ticked circle, match lines between sheets and the skewed wing.

## 11. What exists and what has to be built

| Piece | State |
| --- | --- |
| Estimator workbook and importer (final takeoff, client BOQ) | Exists |
| Synthetic suites and the regression gate | Exist |
| Truth for stages 5 to 7 and 9 in `firebid/evals/schema.py` | Exists |
| Truth for stages 1 to 4, 8 and 10 to 12 | **To add to the schema** |
| `golden.json` reader and the staged comparison (types A to E) | **To build** as a `firebid-eval` suite |
| Export of a bid's stage outputs in the same shape | **To build** (`firebid-eval export-run`) |
| Defect classification and the weighted score in the report | **To build** |
| Semantic comparison (rubric, optional model judge) | **To build**; a model judge waits on decision D2 |
| The brief for drafting a package | Added with this version |

Build order: the schema and exporter first, then exact, tolerance and completeness
comparison on TC-SYN-001, then evidence, then semantic.

## 12. Roles

| Role | Responsibility |
| --- | --- |
| Data owner (D3, not yet named) | What enters the set; confidentiality; when a package is verified |
| Senior Estimator | Verifies stages 4 to 7 and 9 to 10 of each real package; settles ambiguities |
| Product owner | Confirms tolerances, weights and the gate rule; accepts baselines |
| Engineering | The comparison, the exporter, the synthetic packages; never edits a real package's expected values |

## 13. Open decisions

- The tolerances marked "to confirm" in section 6.
- The gate rule in section 8.
- Whether a model may judge semantic matches, and which one (depends on D2).
- Legal's answer on keeping past clients' drawings for evaluation (D3, Q8), which decides
  how many real packages there can be.
