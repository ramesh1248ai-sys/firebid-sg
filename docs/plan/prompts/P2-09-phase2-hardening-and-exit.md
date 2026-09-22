# P2-09 · Phase 2 Hardening and Exit Evaluation

**Builds on:** all Phase 2 steps. **Needs:** assisted-mode pilot tenders and baseline turnaround data (business track). Where the pilot is incomplete, finish the tooling and mark the pilot evidence as pending.

Begin by replying with a plan that maps each item under **Done when** to the work that satisfies it. Wait for approval before changing files.

## Read first

- `docs/plan/project-context.md`: definition of done.
- Requirements: §13.2 (Phase 2 exit criteria), §14 (Phase 2 KPIs: turnaround, price provenance, clarification acceptance), §10 (NFR regression), §16.
- `docs/plan/BUILD_LOG.md` (Phase 2 entries), `docs/reports/phase1-exit.md`.

## Goal

Show whether Phase 2 meets its exit criteria. Confirm the non-functional targets still hold with the heavier estimating workload, and leave the platform ready for the Phase 2 gate review.

## Scope

Phase 2 exit criteria and KPIs; regression of NFR-01, 02, 06, 09, 14, 15; coverage of all Phase 2 requirement IDs.

## Build

1. **KPI instrumentation:**
   - Tender turnaround in working days from receipt to submission-ready, from bid lifecycle timestamps.
   - Price provenance percentage at G2.
   - Clarification acceptance: the share of drafts issued with only minor edits, measured by edit distance between draft and issued text with a configurable threshold.
2. **Exit report** `docs/reports/phase2-exit.md`: KPIs against targets and baseline, pilot findings, gap list with actions, and a recommendation.
3. **Regression:** rerun Phase 1 eval suites and NFR benchmarks with Phase 2 features enabled; load test with pricing, labour and clarification workloads.
4. **Security and privacy review** of the new surfaces: quotations (supplier personal data under PDPA), outcome data and snapshots. Update the data inventory and retention jobs.
5. **Cost and provider review:** compare AI cost per tender by route, provider and model against the target. For the most expensive routes, run `firebid-eval compare-models` across the configured providers and reasoning levels. Propose chain or reasoning changes only where the golden set shows no quality loss and every candidate is approved for the route's data class. Record the evidence for product owner approval.

## Done when

- `docs/reports/phase2-exit.md` reports every Phase 2 KPI and exit criterion against target, with pilot evidence or a pending marker.
- `make req-coverage PHASE=P2` shows every Phase 2 FR with at least one test or a documented manual check.
- Phase 1 eval suites show no regression beyond tolerance, and NFR benchmarks meet targets or appear in the gap list.
- The data inventory and retention jobs cover the Phase 2 entities, with no unresolved high or critical security findings.
- A build log entry is appended with the Phase 2 gate recommendation.
