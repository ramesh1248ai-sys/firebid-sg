# ADR-007: Agent execution and orchestration

- **Status:** Proposed
- **Date:** 2026-09-22
- **Deciders:** Tech Lead; Product Owner consulted
- **Requirements:** §8 (agent principles, autonomy levels, agent contract); FR-BID-06; risk R13

## Context

Through Phase 3, almost every agent is a single structured model call or a short fixed sequence: read a title block, map a legend, extract specification attributes, map BOQ lines, draft a clarification. Human approval waits can last days. Only the Phase 4 Bid Orchestrator coordinates many agents across stages and gates over weeks. Requirements §8.1 calls for orchestration, not a swarm, with deterministic steps wherever possible.

## Options

1. **Plain typed agent functions on the AI gateway, run as queued jobs** (ADR-006). Human waits are `HumanTask` records plus the domain state machines; completing a task queues the continuation job in the same transaction. No framework.
2. **LangGraph from the start.** Graph state and checkpointing are ready-made, but it is heavy for single-call agents. Saved graph state can break when a graph's shape changes, and it is a fast-moving dependency.
3. **Temporal from the start.** Strong durability for long waits, but a cluster and programming model the team does not need until Phase 4.

## Recommendation

**Option 1 now.** Keep the agent base small (typed input and output, evidence and confidence, idempotency key, escalation to `HumanTask`), so an orchestration engine can wrap it later.

At the Phase 3 exit (step P3-03), prototype the S0–S9 bid workflow in two ways: on the existing state machines plus the job queue, and on one engine (LangGraph or Temporal). Compare durability, long waits, visibility, versioning of in-flight workflows, operating cost and team familiarity, and record the decision as ADR-009.

## Consequences

- Phases 0–3 carry no framework dependency, and agents stay easy to test and reason about.
- Multi-step flows are explicit job sequences; their visibility comes from the job history and the AgentRun records.
- Phase 4 may add an engine; if it does, existing agents plug into it unchanged.
