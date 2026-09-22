# P2-03 · Full Specification Analysis and Scope Matrix

**Builds on:** P1-06 (clause tree, citations, attribute extraction), P1-07 (QTO attributes), P2-01 (extended systems). **Needs:** Design Manager review of the obligation categories and interface list (business track).

Begin by replying with a plan that maps each item under **Done when** to the work that satisfies it. Wait for approval before changing files.

## Read first

- `docs/plan/project-context.md`: *proposal*, *evidence record*, "LLM providers and AI usage" (routes, structured outputs, cache hints).
- Requirements: §6.7 (FR-SPEC-02, 03, 04), §6.13 (FR-RSK-01, which consumes the scope matrix next).
- `docs/plan/BUILD_LOG.md`.

## Goal

Turn the specification into a complete, cited list of obligations. Cross-check it against the drawings and takeoff, and produce a scope and interface matrix that shows the estimator what is included, excluded, by others or unclear.

## Scope

FR-SPEC-02, FR-SPEC-03, FR-SPEC-04.

## Build

1. **Obligation extraction** (FR-SPEC-02): a structured schema with categories:
   - testing (hydrostatic pressure and duration), flushing, painting and identification, commissioning;
   - approved makes / AVL, warranty, defects liability and maintenance period, spares, training;
   - submittals (shop drawings, hydraulic calculations, product data), authority inspections and FSC support.

   Each obligation has category, text summary, quantities or durations where stated, and a citation. Run it over the clause tree section by section on a gateway route, with the spec prefix marked by the cache hint.
2. **Cross-check rules** (FR-SPEC-03): deterministic comparisons wherever possible. Examples:
   - spec pipe schedule vs drawing note;
   - spec joining method by DN vs drawing annotation;
   - spec sprinkler type vs legend mapping;
   - spec requires an item (e.g. flow test header) but takeoff has none;
   - drawing shows an item the spec never mentions.

   Use the model only to interpret ambiguous clauses. Each issue carries both citations (clause and sheet evidence), a severity and a category (conflict, missing, ambiguous), and feeds the clarification candidates list.
3. **Scope and interface matrix** (FR-SPEC-04):
   - Per system: status (included, excluded, by others, unclear) for each obligation and interface, e.g. power supply to fire pumps, water supply connection, builder's works and openings, ceiling access panels, painting, electrical interface to fire alarm.
   - Each row links to its clause.
   - Export to xlsx; the estimator edits and confirms.
4. **UI:** obligation list, issue list and matrix, with click-through to clause text and sheet evidence.

## Done when

- The contradiction seeded in the P1-06 synthetic spec is flagged with both citations, along with every other seeded conflict, missing item and ambiguity in an extended fixture. Tagged FR-SPEC-03.
- Every obligation category present in the fixture is extracted with a citation that resolves to clause text. Tagged FR-SPEC-02.
- The scope matrix is generated for the fixture with a status and clause link on each row, and exports to xlsx. Tagged FR-SPEC-04.
- Spec-vs-drawing issues appear as clarification candidates for P2-06.
- A build log entry is appended.
