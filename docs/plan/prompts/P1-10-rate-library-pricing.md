# P1-10 · Rate Library Pricing

**Builds on:** P1-09 (BOQ lines, client BOQ export). **Needs:** the company's current unit-rate list (business track). A synthetic rate list covers the tests.

Begin by replying with a plan that maps each item under **Done when** to the work that satisfies it. Wait for approval before changing files.

## Read first

- `docs/plan/project-context.md`: guardrails 3 and 4, money.
- Requirements: §6.11 (FR-CST-01; FR-CST-03 and FR-CST-09 define the provenance principle this step starts enforcing).
- `docs/plan/BUILD_LOG.md`.

## Goal

Estimators price BOQ lines from a company rate library in which every rate has a source, a date and a validity. Unmatched lines stay visibly unpriced, and the priced client workbook exports cleanly.

## Scope

FR-CST-01.

## Build

1. **Rate library:**
   - Each entry has: item key (canonical type, DN, material, schedule, joining, brand), unit, unit rate (Money), source type (company standard, PO, quotation), source reference, effective date and validity end.
   - xlsx import with validation and error report; version history per entry.
2. **Matching:**
   - Match BOQ lines to rate entries deterministically on item key.
   - For partial matches, the model may propose a candidate entry with reasoning. The rate value always comes from the library entry, and the estimator confirms each proposed match.
3. **Provenance enforcement:** the database and domain layer reject any BOQ line price that lacks a rate-entry reference. Lines without a match show "unpriced".
4. **Validity:** warn on expired entries, and on entries whose validity ends before the tender validity period ends.
5. **Totals:** line amount = quantity × rate in Decimal with half-up rounding; section and grand totals, excluding GST (full GST handling arrives in P2-04).
6. **Priced export:** feed rates into the P1-09 client-workbook export and the company-format BOQ.
7. **Queue weighting:** expose rate-based impact to the P1-08 review queue.

## Done when

- A test shows that setting a price without a rate-entry reference is rejected at the domain layer and at the database. Tagged FR-CST-01.
- Deterministic matches price lines correctly. Proposed matches wait for estimator confirmation. Unmatched lines show "unpriced".
- A test with a rate whose validity ends before the tender validity end raises the warning, and an expired rate raises its warning.
- Totals match hand calculations on a crafted BOQ, including rounding cases.
- The priced client workbook export passes the P1-09 round-trip test with rates filled.
- A build log entry is appended.
