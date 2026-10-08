# `eval/` — the golden set

What the platform's accuracy is measured against.

## Nothing confidential lives here

Tender drawings go to the **eval bucket**, not to git. This directory holds manifests,
checksums and generated artefacts only. See guardrail 8 in `docs/plan/project-context.md`.

## The estimator workbook

Generated rather than committed, so a change to it is a reviewable diff in
`backend/src/firebid/evals/template.py` instead of an opaque binary:

```bash
make golden-template      # writes eval/templates/golden_takeoff.xlsx
```

Send that file to estimators. When one comes back filled in:

```bash
cd backend && uv run firebid-eval import ../path/to/filled.xlsx --out ../eval/truth/<tender>.json
```

It reports every problem at once — by tab, row and column — and writes nothing until the
workbook is clean.

## Who to send it to

`docs/decisions/D3-golden-dataset.md` sets out what to collect, what "verified" has to mean,
and what the data owner is accountable for.

## Title block reading: the `doc_classification` suite

```bash
make eval-docs            # writes eval/results/doc_classification.md
```

Scores the platform's own title block reader (FR-DOC-02: drawing number and revision
accuracy, target at least 95% on vector title blocks) rather than a dummy predictor. Every
file is opened in the sandbox, and OCR needs Tesseract, which the Dev Container, CI and the
sandbox image have; without it the outlined and scanned tenders read nothing.

By default it generates synthetic fixtures: the same seeded set of drawings as CAD, as a PDF
with a text layer, as a PDF with outlined text, and as a 150 dpi scan, reported per tender.
When a real golden set exists it is used instead:

- truth: `eval/truth/doc_classification/<tender>.json` (from `firebid-eval import`)
- files: `eval/files/doc_classification/<tender id>/` (git-ignored: these are client drawings)

The suite has no accepted baseline yet, so `make eval-gate` does not check it. Accept one
once the numbers have been reviewed:

```bash
cd backend && uv run firebid-eval --root ../eval accept --suite doc_classification --approver "Name"
```

## Phase 2 systems: the `p2_systems` suite

```bash
make eval-systems         # writes eval/results/p2_systems.md
make eval-gate            # also compares this suite with eval/baselines/p2_systems.json
```

Scores equipment by type (pumps, tanks, breeching inlets, landing valves, hydrants, hose
reels, test headers, valve sets, air compressors: FR-VIS-04, FR-QTO-06) and its pipe by size,
with the platform's own pipeline. It reads a golden set when one is filed, the same way as
`p1_detection`: truth in `eval/truth/p2_systems/<tender>.json`, drawings in
`eval/files/p2_systems/<tender>/`, one file per sheet named by its drawing number. With none,
it runs on a synthetic tender of four sheets (pump room, typical floor, site plan, riser
schematic).

The estimator workbook lists the new object types. `p1_detection` reports
`equipment_count_accuracy` too, for a golden set whose truth counts equipment.

The baseline in `eval/baselines/p2_systems.json` was written by the build step and names it
as approver. Accept it under a person's name once the product owner has looked at it:

```bash
cd backend && uv run firebid-eval --root ../eval accept --suite p2_systems --approver "Name"
```

## Golden reference packages: `export-run` and `golden`

A package (`eval/golden/synthetic/<TC_ID>/`, or `eval/golden/real/<TC_ID>/`, which git
ignores) holds the expected work product of each stage: see `docs/plan/TEST_STRATEGY.md`.
To set a bid against one:

```bash
cd backend
uv run firebid-eval export-run --bid <the bid's UUID> --out ../eval/results/golden/run.json
uv run firebid-eval --root ../eval golden --case TC-SYN-001 \
    --run ../eval/results/golden/run.json --report ../eval/results/golden/TC-SYN-001.md
```

`export-run` reads what the platform stored for the bid at stages 1 to 7 and works nothing
out again. Its file holds the bid's quantities: for a real tender it is confidential, like
the bid. `golden` writes the differences by class (critical, high, medium, low), those to
settle against a recorded ambiguity, what the run has that the reference does not, and a
score over the dimensions that were measured. It exits non-zero on a critical or a high
defect. A stage that is not compared is reported as not compared, never as passed.

In CI the same comparison runs for the three synthetic packages as database tests
(`backend/tests/db/test_golden_run.py`), which list every known difference, case by case.
