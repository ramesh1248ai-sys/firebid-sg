# Brief: a Golden Reference Work Product Package for FireBid SG

How to use this brief (see `docs/plan/TEST_STRATEGY.md`, section 5):

- Give it, with the test case's input documents, to a **fresh** session or to an estimator.
  Do not give it the platform's output for the same tender.
- Fill in the "Input test case" section. Everything else is already filled for FireBid SG.
- The result is a **draft** until an estimator verifies it. For counts and pipe lengths on
  real drawings the estimator workbook is the source, and the package cites it.
- A package made from a real tender is confidential: store it under `eval/golden/real/`,
  which git ignores, and in the eval bucket.

---

# Role

You are an **AI Application Test Architect and Domain SME** (fire protection estimating,
Singapore) creating an independent **Golden Reference Dataset** for testing an agentic AI
application.

The application under test (AUT) processes a tender through several stages and produces
intermediate work products and a final output. Independently analyse the same input and
produce the expected work product at **each stage**.

These outputs are not the application's logic. They are independent test oracles to compare
with what the application produces.

# Application under test

- **Application name:** FireBid SG
- **Description:** an AI-assisted tendering platform for fire-protection contractors. It
  reads a tender's drawings, specification and client BOQ; registers drawings and
  revisions; finds each drawing's views and scale; reads legends and proposes what each
  symbol is; counts devices and measures pipe; builds and prices a bill of quantities;
  drafts clarifications, risks and qualifications; and assembles a review pack for gated
  approval. Geometry, counting, measurement and money are deterministic code. Models
  classify, map and draft, and a person confirms every mapping before anything is counted.
- **Business domain:** fire protection (sprinkler, wet and dry riser, hose reel, fire alarm)
  estimating and tendering for building projects in Singapore.
- **Primary objective:** a complete, priced and reviewed bid whose every quantity and price
  can be traced to its source, in less time than a manual takeoff.

# Input test case

- **Test case ID:** `<TC_ID>`
- **Description:** `<TEST CASE DESCRIPTION>`
- **Input files:** `<LIST: drawings (PDF or DXF), specification, client BOQ workbook, rate list, quotations>`
- **Input data:** `<bid details: project, client, consultant, deadlines; anything not in the files>`
- **Applicable business rules** (delete those the test case does not exercise):
  - Only the current revision of a drawing is taken off; superseded revisions are kept and
    not counted.
  - A symbol is counted only when its legend row is mapped to an object type and a person
    has confirmed the mapping.
  - An item drawn on a general arrangement and again on an enlarged plan is counted once.
  - Pipe is measured net along the centreline, through fittings and valves, with no
    allowance for waste, unless the measurement conventions for the bid say otherwise.
  - Sprinkler drops, fittings and hangers not drawn are derived by rule and identified as
    derived.
  - Every price has a source (company standard, quotation or purchase order) that is valid
    on the pricing date.
  - GST is 9%.
  - `<OTHER RULES FOR THIS CASE>`
- **Applicable standards and regulations:** SS CP 52 (automatic fire sprinkler systems),
  SS 575 (fire hydrant, rising mains and hose reel systems), SS 645 (electrical fire alarm
  systems), the SCDF Fire Code, and the tender's own specification. `<OTHERS>`

# Expected workflow stages

1. Document intake: each file's kind, sheets, accepted or refused.
2. Title blocks and registers: drawing number, title, revision, date, level; which
   revision is current.
3. Views and scale: each sheet's views, stated scale, whether the scale is proved and how.
4. Legends and symbols: each legend row's description, the letters in its symbol, the
   object type it is.
5. Object detection: counts by object type for each sheet, with locations.
6. Pipe network: length by nominal diameter for each sheet.
7. Takeoff: bid totals after duplicates between sheets are settled; rule-derived items.
8. Specification: obligations with their clause; issues against the drawings; scope matrix.
9. Bill of quantities: our bill; each client BOQ line mapped; variances.
10. Pricing and labour: each line's rate and source; labour; the cost build-up.
11. Clarifications, risks and qualifications.
12. Final output: the review pack (totals with and without GST, gate states) and the
    submission's contents.

Cover only the stages the test case's inputs allow. Say which you left out and why.

Object types use the platform's keys, for example `sprinkler_pendent`, `sprinkler_upright`,
`sprinkler_sidewall`, `sprinkler_concealed`, `gate_valve`, `check_valve`, `butterfly_valve`,
`flow_switch`, `tamper_switch`, `landing_valve`, `hose_reel`, `hydrant`, `breeching_inlet`,
`fire_pump`, `jockey_pump`, `fire_water_tank`, `pipe`, `fitting`, `not_an_object`.

# Your task

Independently perform the complete analysis of the supplied input. For **each** stage,
produce the expected intermediate work product that a correctly working application should
produce. Do not give only the final answer. Establish a traceable chain:

**Input → work product → work product → … → final output**

Each work product must be detailed enough for the application's actual output to be
compared with it.

# Required output structure

## 1. Test case summary

Test case ID, objective, input summary, expected business outcome, key assumptions,
applicable rules and standards, expected final result.

## 2. Golden intermediate work products

For every stage:

- **Purpose:** what the stage is expected to accomplish.
- **Input used:** exactly which input is processed.
- **Expected processing:** concise, auditable decision logic, rules, calculations, evidence
  and conclusions. No private chain of thought.
- **Golden work product:** the expected structured output, as a table or JSON:

```json
{
  "stage": "<STAGE_NAME>",
  "status": "SUCCESS",
  "entities": [],
  "findings": [],
  "exceptions": [],
  "confidence": 0.0,
  "source_references": []
}
```

- **Evidence / source reference:** for every important value or conclusion, the source
  document; the sheet (drawing number and revision); the view, grid reference or sheet
  coordinates in millimetres; the legend row; the specification clause; the rule or
  standard.
- **Expected validation rules:** conditions that hold if the work product is correct (for
  example: every sheet has exactly one current revision; counts by sheet sum to the bid
  total less settled duplicates).
- **Expected exceptions:** errors, ambiguities and missing information the stage should
  raise (for example: a sheet marked not to scale; a symbol on a plan that no legend shows).

## 3. Stage-to-stage traceability

| Stage | Input | Output artifact | Used by next stage | Key validation |
| --- | --- | --- | --- | --- |

Every important final conclusion must trace back to its originating input.

## 4. Golden final output

The final output as the application is expected to produce it: expected result, supporting
evidence, calculations, business rules applied, exceptions and risks.

## 5. Comparison criteria

- **A. Exact match:** drawing numbers, revisions, current-revision flags, object type of
  each legend row, BOQ line mappings, prices, totals, gate states.
- **B. Semantic match:** descriptions, obligations, clarification wording, risks.
- **C. Tolerance-based match:** give the expected value, tolerance and acceptable range.
  Defaults: sprinkler count at least 98% accurate; pipe length by diameter within 5%;
  missed items at most 5%; money exact to the cent.
- **D. Evidence match:** the right document, sheet, location, clause, rule or standard.
- **E. Completeness match:** all expected legend rows, object types, issues, exceptions,
  requirements and recommendations.

## 6. Golden comparison dataset

```json
{
  "test_case_id": "<TC_ID>",
  "stages": [
    {
      "stage_id": "STG-001",
      "stage_name": "<NAME>",
      "comparison_type": "EXACT|SEMANTIC|TOLERANCE|EVIDENCE|COMPLETENESS",
      "expected_output": {},
      "mandatory_fields": [],
      "allowed_variations": [],
      "tolerance": null,
      "minimum_confidence": null
    }
  ],
  "final_output": {
    "expected_result": {},
    "mandatory_fields": [],
    "allowed_variations": []
  }
}
```

Stage IDs are `STG-001` to `STG-012` in the order above; a stage left out keeps its number.

## 7. Defect classification rules

- **Critical:** an incorrect business decision or a materially unsafe or non-compliant
  result (a wrong total; a legend row as the wrong object type; sprinklers under-counted
  beyond tolerance; a superseded revision taken off).
- **High:** important data, calculation, rule evaluation or finding is wrong.
- **Medium:** a meaningful intermediate result differs without changing the final outcome.
- **Low:** minor wording, formatting or ordering.
- **Acceptable variation:** different from the reference but equivalent, and within the
  business rules.

## 8. Evaluation score

| Dimension | Weight |
| --- | ---: |
| Data extraction accuracy | 20% |
| Intermediate work product accuracy | 20% |
| Business rule accuracy | 20% |
| Calculation accuracy | 15% |
| Evidence / traceability | 10% |
| Completeness | 10% |
| Final output quality | 5% |

Overall test score = the weighted score across all dimensions. Also report the number of
critical, high, medium and low defects and of acceptable variations.

## 9. Test oracle requirements

The Golden Reference must be independent of the application's implementation. Do **not**:

- assume the application's answer is correct;
- reverse-engineer the application's output;
- copy the application's intermediate results;
- adjust the expected answer to match the application;
- hide uncertainty;
- invent information that is not in the source material.

Where the input is ambiguous, state: the expected interpretation, the alternative
interpretation, the impact of the ambiguity, and the information needed to resolve it.

Say in the package who or what produced it, from which files (with checksums), and that the
application's output was not available to the author.

## 10. AI-specific evaluation

Give criteria for each: hallucination (output no source supports), grounding (each
conclusion has source evidence), consistency (the same input gives consistent work
products), completeness (what the application must not miss), instruction following (the
business rules and workflow, including that only a person confirms a mapping), agent
handoff quality (each stage's output has what the next needs), and error propagation
(whether an early error causes later ones).

## 11. Final golden dataset

A consolidated dataset with: test case ID; input; the expected output of each stage; the
expected final output; expected evidence; validation rules; comparison method; tolerance;
severity if incorrect. It must be fit to store as a golden test case and to compare
automatically with the application's results.

# Important constraints

- No private chain of thought; concise, auditable reasoning summaries instead.
- Distinguish facts from assumptions.
- Cite source evidence wherever possible.
- Do not invent missing information. If a count cannot be made reliably from the material
  given (for example symbols on a large drawing at the resolution available), say so and
  leave the value out for an estimator to supply; do not estimate it.
- Preserve exact numerical values.
- State uncertainty explicitly.
- Every final conclusion traces to an input or a validated business rule.
- Prefer structured JSON and tables.
- Use deterministic terminology and the field names and object type keys given above.
- The reference is what a correctly working application **should** produce, not what the
  application currently produces.

# Deliverable

The complete **Golden Reference Work Product Package**:

1. Test case summary
2. Expected intermediate work products
3. Stage-to-stage traceability
4. Golden final output
5. Comparison criteria
6. Machine-readable golden dataset
7. Defect classification
8. Evaluation score
9. AI-specific evaluation criteria
10. Test oracle
11. Known ambiguities and assumptions
