# P0-04 · Multi-Provider AI Gateway and Agent Runtime

**Builds on:** P0-02 (AgentRun, HumanTask, audit). **Needs:** decision D2 / ADR-004 for which providers are approved in production and for which data classes. Until then, build all four adapters and approve only the fake adapter for `confidential` and `commercial` data outside development.

Begin by replying with a plan that maps each item under **Done when** to the work that satisfies it. Wait for approval before changing files. This is an L step: commit per sub-part (interface and config, each adapter, router and policy, metering and tracing, agent runtime).

## Read first

- `docs/plan/project-context.md`: "LLM providers and AI usage" (binding design), guardrails 2, 3 and 8.
- Requirements: §8 (agent principles, autonomy levels, agent contract), §11.4 (data usage policy), FR-ADM-05, NFR-05, NFR-11, NFR-14, NFR-15, risk R13 (vendor lock-in).
- `docs/adr/ADR-004*`, `docs/plan/BUILD_LOG.md`.

## Goal

Every model call in the platform goes through one governed gateway. Callers name a route. Configuration picks the provider and model chain, and the gateway checks capabilities, enforces data-class approval on every attempt, falls back across providers when one fails, meters cost, and records provenance. Agents built on the runtime follow the agent contract in requirements §8.4.

## Scope

FR-ADM-05, NFR-05, NFR-11, NFR-14, NFR-15, agent contract (§8.4).

## Build

1. **Provider-neutral interface** (`ai_gateway/`):
   - Request types: messages with text, image and document parts; tool definitions and tool calls; structured-output requests bound to a Pydantic model; reasoning level; cache hints; limits.
   - A normalised response: content, parsed object, tool calls, usage, and a stop reason (`end`, `max_output`, `refusal`, `tool_call`, `content_filter`, `error`). The provider's raw request ID is kept for support.
   - Operations: `generate`, `generate_structured`, `stream`, `submit_batch`/`poll_batch`, and `embed` (embedding routes).
2. **Adapters** (`ai_gateway/providers/`), each built on the provider's official SDK and following its current documentation:
   - **Anthropic.** Load the `claude-api` skill first. Covers the Claude API plus Amazon Bedrock, Google Vertex AI and Microsoft Foundry through the SDK's platform clients, selected by provider config. Apply the skill's defaults (adaptive thinking, `effort` mapped from the reasoning level, refusal handling with fallbacks). Use native structured outputs, PDF documents, prompt caching and message batches.
   - **OpenAI:** the OpenAI API and Azure OpenAI.
   - **Google:** Gemini through the Google Gen AI SDK, for the Gemini API and Vertex AI.
   - **OpenAI-compatible:** for self-hosted open-weight models (e.g. vLLM), with conservatively declared capabilities.
   - **Fake adapter:** deterministic and scriptable, for tests and CI.
3. **Configuration** (`backend/config/llm.yaml`, the shape in project-context):
   - Providers, models (capabilities, limits, effective-dated prices) and routes.
   - Per-environment overlays; secrets by reference, resolved from environment or vault.
   - Validated by a Pydantic schema at startup, with a config-version hash.
   - Startup fails with a clear message when a route's chain includes a model missing a required capability, or a provider not approved for the route's data class.
   - Seed the configuration with the routes P0 and P1 need. Every route starts with primary `claude-opus-5`; fallback entries are commented out until ADR-004 approves a second provider.
4. **Router and policy:**
   - Resolve a route to its chain.
   - Retry with backoff on retryable errors; keep a circuit breaker per provider and model; fall back to the next model on retryable errors, rate limits or an open breaker.
   - Check the data class before every attempt; when no approved model remains, escalate to a `HumanTask`.
   - A refusal or a schema failure gets one retry on the same model, then escalates.
   - Log every attempt with provider and model.
4a. **Efficiency:**
   - **Response cache** in PostgreSQL, with large bodies in object storage. It is keyed by route, model, prompt version and a fingerprint of the normalised input, and holds only data the route's data class allows. A hit returns the stored response and records `cache_hit` on the call. A `regenerate` flag bypasses it. Retention is configurable per data class.
   - **Shared rate limiter** in PostgreSQL: token buckets for requests and tokens per minute, per provider and model, from limits in `llm.yaml`. Every worker process draws from it. Calls wait for capacity up to a timeout, then fall back like a rate-limit error.
5. **Capability emulation:** when a route needs a capability the selected model lacks and the route allows emulation:
   - render PDF pages to images;
   - obtain structured output through a single tool call;
   - run a batch as rate-limited sequential calls;
   - ignore cache hints.

   Record emulation on the call record.
6. **Prompt registry:** `ai_gateway/prompts/<route>/v<N>.md`, with front-matter (route, purpose, owner) and optional model-family variants `v<N>.<family>.md`. The version is the content hash, and every call records it (NFR-11).
7. **Metering** (FR-ADM-05, NFR-15):
   - Normalised token usage and cost from the configured prices, attributed to bid, route, provider, model and agent run.
   - A per-bid budget with alert thresholds.
   - A per-run budget; exceeding it pauses the run and alerts the owner.
8. **Tracing** (NFR-14):
   - OpenTelemetry spans for every attempt and tool call, with route, provider, model, attempt number, fallback reason and config version.
   - The `AgentRun` record (input hash, prompt version, tool calls, output reference, latency, cost, errors).
   - A redaction filter keeps document text and prices out of logs and spans.
   - An **opt-in debugging payload store**, enabled per bid by an administrator: prompts and responses encrypted at rest, deleted after a configurable retention (default 14 days), readable only by named administrators, with every read audited.
9. **Agent contract base** (§8.4):
   - Typed input and output, where the output carries an evidence record and confidence per assertion.
   - An idempotency key.
   - Validation before persisting.
   - Timeout and retry limits, then escalation to a `HumanTask` with context.
   - Outputs persisted as proposals with provenance, including provider and model.
   - Writes only through domain services, which reject changes to baselined or approved records.
10. **Autonomy levels** (§8.2): each agent tool declares its level (L0–L2). The runtime refuses to register any tool declared L3.
11. **Agent execution** (ADR-007):
    - Agents are plain typed functions that call `ai_gateway` routes, run as jobs on the PostgreSQL queue. Every agent job is idempotent on its idempotency key.
    - A multi-step agent is a short, explicit sequence of such jobs.
    - A step needing a person creates a `HumanTask`. Completing the task queues the continuation job in the same transaction.
    - No agent framework is added. Keep the agent base small enough that the Phase 4 orchestration decision (LangGraph vs Temporal vs the existing state machines) can wrap it later.
12. **Admin view:**
    - providers, models and routes (read-only, from config), with approved data classes;
    - circuit-breaker state and error rate per provider;
    - cost by bid, route, provider and model.
13. **Adapter contract test suite:** one suite every adapter must pass on recorded fixtures, covering:
    - text generation, structured output, image input, document input and a tool call;
    - streaming;
    - usage mapping;
    - stop-reason mapping, including refusal and content filter;
    - error mapping (rate limit, 5xx, auth, bad request).

    Live tests carry `@pytest.mark.live` plus the provider name, and run only with that provider's credentials.
14. **Demonstration agent:** `title_block_reader` reads a title-block image and text and returns a structured result with confidence, on route `title_block_read`. P1-02 reuses it.

## Done when

- The contract suite passes for the Anthropic, OpenAI, Google, OpenAI-compatible and fake adapters on recorded fixtures.
- Switching `title_block_read` to a different configured model is a config-only change: the same agent test passes with two differently configured providers (using fixtures) and records the provider and model used. Tagged NFR-11.
- Config validation tests: startup fails for a route whose model lacks a required capability, and for a chain containing a provider not approved for the route's data class. Tagged NFR-05.
- Fallback tests cover four cases:
  - a primary returning 5xx, a timeout or a rate limit is served by the next model, with the fallback reason on the span;
  - the circuit breaker opens after the configured failures and closes after cooldown;
  - a `confidential` route never reaches an unapproved provider even when every approved model fails, and escalates instead (NFR-05, NFR-08);
  - a route using an emulated capability records the emulation.
- Tests cover the remaining unhappy paths:
  - exceeding a run budget pauses the run and raises an alert (NFR-15);
  - a refusal escalates to a HumanTask after one retry;
  - a schema-invalid output escalates to a HumanTask after one retry;
  - registering an L3 tool fails;
  - an attempt to modify a baselined record is rejected.
- A test shows log lines and spans produced while processing a document contain no document text or price values. Tagged NFR-14.
- Cache tests:
  - an identical second call is served from cache with `cache_hit` recorded and no adapter call;
  - changing the prompt version or model misses the cache;
  - `regenerate` bypasses it.
- A rate-limiter test runs parallel worker processes against a fake provider limit, and the observed request rate stays within the limit.
- Payload store tests:
  - payloads are stored only for opted-in bids;
  - reads by non-administrators are refused;
  - every read is audited;
  - expired payloads are deleted.
- An agent job that creates a `HumanTask` resumes when the task is completed. Re-running the same job with the same idempotency key creates no duplicate proposal.
- Cost per bid, broken down by provider and model, is queryable and shown on the admin page. Tagged FR-ADM-05.
- Live smoke tests pass for each provider whose credentials are present, and are skipped otherwise.
- A build log entry is appended, listing the configured providers and each one's approved data classes.
