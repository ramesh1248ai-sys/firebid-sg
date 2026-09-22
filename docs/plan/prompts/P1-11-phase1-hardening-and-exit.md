# P1-11 · Phase 1 Hardening, Pilot Support and Exit Evaluation

**Builds on:** all Phase 1 steps. **Needs:** decision D2 / accepted ADR-004 (hosting), the golden set, and 3 live tenders selected for shadow mode (business track). Where the pilot has not run yet, finish the tooling and mark the pilot evidence as pending.

Begin by replying with a plan that maps each item under **Done when** to the work that satisfies it. Wait for approval before changing files.

## Read first

- `docs/plan/project-context.md`: definition of done.
- Requirements: §10 (NFR-01 to 07, 09, 14, 15), §13.2–§13.3 (Phase 1 exit criteria, shadow mode), §14 (Phase 1 KPIs), §16 (risks R1, R4, R9, R10).
- `docs/plan/BUILD_LOG.md` (every Phase 1 entry, including recorded gaps).

## Goal

Make Phase 1 ready for a gate review. Measure it against the exit criteria, support a shadow pilot on live tenders, close the non-functional gaps, and deploy it in a Singapore region with backups, monitoring and runbooks.

## Scope

NFR-01, 02, 03, 04, 05, 06, 07, 09, 14, 15. Evidence for the Phase 1 exit criteria and KPIs. The rest of the Phase 1 requirement IDs (coverage check).

## Build

1. **Exit evaluation:** run the full golden-set suite. Produce `docs/reports/phase1-exit.md` with each Phase 1 KPI against its target, sliced by input class and consultant, and a gap list with causes and proposed actions.
2. **Shadow mode:**
   - Import an estimator's manual takeoff for a live tender, plus time spent.
   - Compare it with the AI-assisted result line by line, as a report.
   - Capture time on task from workbench activity, and review and verification time per tender, for the QTO effort KPI.
3. **KPI dashboard:** Phase 1 KPIs, escalation rate, workflow and tool success rate, and cost per bid.
4. **Performance and load** (NFR-01, 02):
   - Benchmark ingest and classify of 300 sheets (target ≤ 1 h) and first-pass QTO for 50 fire protection sheets (target ≤ 4 h).
   - Load test 10 concurrent bids and 20 users.
   - Profile and fix the top bottlenecks.
5. **Security** (NFR-06):
   - Dependency and container scanning plus SAST in CI.
   - Complete the ASVS Level 2 checklist from P0-03.
   - Secrets audit.
   - A pen-test scope document.
6. **Privacy and retention** (NFR-07, NFR-09): regenerate the data inventory; add retention and archival jobs per configurable policy; verify the audit hash chain in a scheduled job.
7. **Deployment** (NFR-03, 04, 05), with Terraform in the Singapore region per ADR-004 plus a new ADR-008 for hosting:
   - separate scalable pools for API, job workers and the parser sandbox, with the sandbox's network isolation and resource limits enforced by the platform;
   - connection pooling (PgBouncer or the managed database proxy) sized for all pools;
   - staging and production;
   - managed PostgreSQL (version per ADR-001) with point-in-time recovery, plus monitoring of partition creation and job-queue depth;
   - an object-lock bucket for snapshots;
   - availability monitoring and alerts;
   - a deployment guard that blocks planned maintenance within 48 hours of any active bid's submission deadline;
   - a restore drill in staging, with timings recorded against RPO ≤ 24 h and RTO ≤ 8 h.
8. **Cost and providers** (NFR-05, NFR-15):
   - A per-bid AI cost report broken down by route, provider and model, with the Phase 0 target cost per tender compared against actuals.
   - For each provider enabled in production `llm.yaml`, confirm its region, retention and no-training terms match the data classes it is approved for (ADR-004).
   - A game-day test: disable the primary provider in staging and confirm routes with an approved fallback keep working, while the others escalate cleanly.
9. **Runbooks and user material:** deploy, restore, incident response, key rotation; an estimator quick-start guide and a one-day training outline.

## Done when

- `docs/reports/phase1-exit.md` exists, with every Phase 1 KPI and exit criterion reported against target, and a gap list.
- `make req-coverage PHASE=P1` shows every Phase 1 FR and the NFRs above with at least one test, or a documented manual check in the build log.
- Benchmark and load results are recorded against NFR-01 and NFR-02, and any misses have actions in the gap list.
- The restore drill has run in staging, with measured RPO and RTO recorded. The deployment guard blocks a maintenance window near a seeded deadline. Tagged NFR-03 and NFR-04.
- Security scans run in CI with no unresolved high or critical findings, and the ASVS checklist is complete. Tagged NFR-06.
- The shadow-mode comparison report exists, for at least one live tender if the pilot has started, or for a synthetic tender otherwise.
- Production is deployed in the Singapore region, and the provider data-terms check is recorded for every enabled LLM provider. Tagged NFR-05.
- The provider-outage game day is run in staging and its result recorded. Tagged NFR-03.
- A build log entry is appended with the recommendation for the Phase 1 gate review.
