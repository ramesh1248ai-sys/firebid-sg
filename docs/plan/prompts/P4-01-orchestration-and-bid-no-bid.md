# P4-01 · End-to-End Orchestration and Bid/No-Bid

**Builds on:** every agent and gate from Phases 0–3, P0-04 (agent runtime, autonomy levels), ADR-009 (orchestration engine chosen at the Phase 3 exit). **Needs:** bid/no-bid criteria from the Commercial Director (business track).

Begin by replying with a plan that maps each item under **Done when** to the work that satisfies it. Wait for approval before changing files. Model calls go through `ai_gateway` routes, and any new route is declared in `llm.yaml`.

## Read first

- `docs/plan/project-context.md`: guardrails 2 and 7, *gate*.
- Requirements: §5 (stages S0–S9, gates, addenda loop, deadline control), §8 (principles, autonomy levels, agent catalogue), §6.1 (FR-BID-05, 06).
- `docs/plan/BUILD_LOG.md`.

## Goal

The Bid Orchestrator runs a tender from receipt to submission-ready. It schedules agents and tasks, pauses at every human gate, re-enters only affected stages when an addendum arrives, and warns owners before deadlines slip. Bid/no-bid becomes a recorded G0 decision.

## Scope

FR-BID-05, FR-BID-06.

## Build

1. **Bid workflow** (FR-BID-06): implement stages S0–S9 on the orchestration approach recorded in ADR-009.
   - Steps invoke existing agents and pipelines as jobs.
   - Gates G0–G4 are durable waits that create approval tasks for the Accountable role, and resume on decision.
   - Workflow state is durable, so a run survives restarts and deployments.
   - A workflow definition change never breaks bids already in flight; follow ADR-009's versioning approach.
2. **Addenda loop:** an addendum event triggers delta analysis (P2-02). The graph re-enters only affected stages and re-requests only affected gate confirmations.
3. **Autonomy enforcement:**
   - The orchestrator's tools are L0–L2 only: classify, mark unambiguous supersessions, re-process, create tasks, send internal reminders.
   - An allow-list test fails the build if any tool can transmit externally, submit to an authority, commit contractually, or modify approved data.
4. **Deadline control:** estimate remaining effort per stage from historical task durations, compare it with time to the clarification cut-off and the submission deadline, and alert owners when a stage is at risk.
5. **Bid/no-bid** (FR-BID-05):
   - A qualification form: project type, value band, systems, capacity, key risks, client history.
   - An AI summary drawn from tender documents and company history (L1 proposal).
   - A G0 decision by the Commercial Director with rationale. Estimating tasks are assigned only after G0.
6. **Observability:** a run timeline view per bid (stages, agent runs, gates, waits, costs).

## Done when

- An end-to-end test on a synthetic tender runs S0 to G4-ready, with simulated approvals at each gate, recovery after a forced restart mid-stage, a deployment of a changed workflow definition while the run is waiting at a gate, and one addendum that re-opens only the affected stages. Tagged FR-BID-06.
- The allow-list test proves no orchestrator tool is L3.
- The deadline-risk alert fires in a time-frozen test where remaining effort exceeds the time available.
- G0 is required before estimating tasks are assigned, and records decision, approver and rationale. Tagged FR-BID-05.
- A build log entry is appended.
