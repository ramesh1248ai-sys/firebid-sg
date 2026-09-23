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
