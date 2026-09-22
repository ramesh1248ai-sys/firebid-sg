# P3-03 · Manpower View, Phase 3 Hardening and Exit Evaluation

**Builds on:** P2-05 (labour hours), P3-01, P3-02. **Needs:** a coordination pilot on live projects with models (business track). Mark it pending if not yet run.

Begin by replying with a plan that maps each item under **Done when** to the work that satisfies it. Wait for approval before changing files.

## Read first

- `docs/plan/project-context.md`: definition of done.
- Requirements: §6.12 (FR-LAB-04), §13.2 (Phase 3 exit criteria), §14 (coordination precision, citation accuracy), §16 (R6 regulatory knowledge staleness).
- `docs/plan/BUILD_LOG.md` (Phase 3 entries).

## Goal

Add the crew-days and manpower view for programme sanity checks. Show whether Phase 3 meets its exit criteria, and harden the compliance corpus operations so the knowledge stays current.

## Scope

FR-LAB-04; Phase 3 exit criteria; corpus freshness operations.

## Build

1. **Manpower view** (FR-LAB-04): crew-days per system and zone from labour hours and configurable crew sizes, and a manpower histogram over an estimator-entered programme window. Export to xlsx.
2. **Corpus freshness:**
   - A scheduled check lists corpus documents past a review interval.
   - A register records SCDF circulars and amendments entered by the knowledge owner.
   - Findings that cite a superseded edition while the project is on a newer one are flagged.
3. **Exit report** `docs/reports/phase3-exit.md`: coordination precision and citation accuracy against target, pilot findings, gap list and recommendation.
4. **Regression:** earlier eval suites and NFR benchmarks with Phase 3 features enabled; security review of BIM ingestion (parser hardening, file limits).
5. **Phase 4 orchestration decision** (revisits ADR-007): prototype the S0–S9 bid workflow skeleton, with gates as long human waits and an addendum re-entry, in two ways:
   - the existing domain state machines plus the PostgreSQL job queue;
   - one engine, LangGraph or Temporal, chosen by a short options review.

   Compare them on durability across restarts, handling of waits lasting days to weeks, visibility of workflow state, versioning of in-flight workflows when the definition changes, operational cost and team familiarity. Record the decision as ADR-009.

## Done when

- Crew-days and the histogram match hand calculations for a crafted labour set and export to xlsx. Tagged FR-LAB-04.
- The freshness check flags an overdue document, and a finding that cites a superseded edition.
- `docs/reports/phase3-exit.md` reports each Phase 3 exit criterion.
- `make req-coverage PHASE=P3` shows every Phase 3 FR covered.
- Earlier suites show no regression beyond tolerance.
- ADR-009 records the Phase 4 orchestration decision with the prototype comparison.
- A build log entry is appended with the Phase 3 gate recommendation.
