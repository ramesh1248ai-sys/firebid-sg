# FireBid SG: Project Context for Build Agents

This file is binding for every build step. It holds the architecture, conventions and guardrails that all steps share. Step prompts in `docs/plan/prompts/` point here instead of repeating it. When a step changes a convention, update this file in the same change and record the reason in an ADR.

## Product in one paragraph

FireBid SG is an AI-assisted tendering and pre-construction platform for Singapore fire protection contractors. It turns tender drawings, specifications and client BOQs into verified, evidence-traceable quantities, BOQs, estimates, clarifications and bid-risk assessments. Accountable people make every engineering, regulatory and commercial decision. The platform proposes; estimators, managers and approvers decide.

- Requirements: `docs/requirements/FireBid_SG_Requirements_v2.md` (generated from the approved `.docx`). Requirement IDs such as `FR-QTO-08` and `NFR-06` are the unit of scope, test and sign-off.
- Plan and step order: `docs/plan/IMPLEMENTATION_PLAN.md`.
- What earlier steps built: `docs/plan/BUILD_LOG.md`. Read it before planning, and reuse existing modules.

## Leading words

These words carry specific meaning across code, prompts and docs. Use them consistently.

- **Evidence record:** the provenance attached to every quantity, price and finding: source file, sheet, revision, page, region or geometry reference, extraction method, calculation method, confidence and verification status (requirements Appendix B).
- **Proposal:** any AI- or rule-generated output awaiting a person's decision. Proposals are stored with provenance (model, prompt version, rule version, run ID, input hash). Only a named user action turns a proposal into a verified or approved record.
- **Gate:** a human approval point (G0–G4, requirements §5). A gate records the approver, time and the state it approved.
- **Deterministic-first:** geometry, measurement, counting, unit conversion and money arithmetic run in tested Python code. Models interpret, classify, map and draft.
- **Golden set:** historical tenders with verified quantities and BOQs, used by the evaluation harness (`firebid-eval`) to measure accuracy.
- **Source-type label:** one of the six labels in requirements §2.3, attached to every compliance statement.

## Architecture

| Concern | Choice | Notes |
|---|---|---|
| Backend | Python 3.12 (pinned), FastAPI, Pydantic v2 | 3.12 because geometry and BIM libraries publish pre-built packages for it first. Package manager `uv`; lint `ruff`; types `mypy` (strict in `domain/`). |
| Persistence | PostgreSQL 17 (or the newest version the managed service supports in Singapore) + pgvector; SQLAlchemy 2.0 ORM with **synchronous** sessions on psycopg 3; Alembic | Async only for streaming endpoints (progress via SSE). Row-level security on bid-scoped tables. Large tables partitioned. Connection pooling via PgBouncer or the managed proxy. Geometry via Shapely in app code with bbox columns indexed; adopt PostGIS only via ADR. |
| Background work | PostgreSQL-backed job queue (Procrastinate, per ADR-006) | Jobs are queued in the same transaction as the data they process. Retries, periodic jobs and job history live in SQL. Workers are processes, because CPU-bound work does not share threads well in Python. No Redis: shared counters such as rate limits also live in PostgreSQL unless measurements justify Redis via ADR. |
| Agent execution | Typed Python functions on `ai_gateway`, run as queued jobs (ADR-007) | Human waits use `HumanTask` records and the domain state machines. The Phase 4 orchestration engine (e.g. LangGraph or Temporal) is re-decided at the Phase 3 exit. |
| LLM providers | `ai_gateway`: a provider-neutral interface with adapters over each provider's official SDK: Anthropic, OpenAI / Azure OpenAI, Google Gemini / Vertex AI, and OpenAI-compatible self-hosted endpoints. Routing is set in `backend/config/llm.yaml`. | Details in "LLM providers and AI usage" below |
| Object storage | S3 API (SeaweedFS locally, per ADR-001), `boto3` | Originals immutable; submission snapshots under object lock |
| PDF | `pypdfium2` for rendering, and for vector paths and text through the PDFium page-object API; `pdfplumber` as fallback | pdfminer-based parsing is too slow on dense drawings. PyMuPDF is AGPL/commercial: use it only if ADR-002 records a purchased licence (decision after the P1-03 benchmark). |
| DWG/DXF | Converter interface (ODA File Converter default, licence per ADR-003); `ezdxf` for DXF | |
| Untrusted files | Sandboxed parser workers: no network, CPU/memory/time limits, read-only filesystem except scratch | Malware scan on upload; `defusedxml`; image pixel limits; archive limits (size, file count, nesting). LibreOffice headless in the sandbox converts legacy `.doc`/`.xls`. |
| Raster text | Tesseract OCR | Vision model for interpretation, never for final counts |
| Office files | `openpyxl` to read; client workbooks written by patching only the target cells in the workbook XML; `python-docx` | openpyxl drops images, charts and other objects when it re-saves a workbook, so it never writes a client file |
| Frontend | React 19 + TypeScript + Vite, TanStack Query / Table / Virtual, React Router, Tailwind + shadcn/ui | API client generated from the backend's OpenAPI schema; CI fails when it is out of date |
| Drawing viewer | OpenSeadragon deep-zoom tiles + Canvas/WebGL overlay with a spatial index for hit-testing | Low zoom levels pre-rendered at ingest; close-up tiles rendered on first view and cached; WebP. SVG only for selected or hovered items. |
| Auth | OIDC: Microsoft Entra ID in staging and production; Keycloak in docker-compose with claims mapped to Entra's token shape | MFA enforced at the IdP. Identity comes from the IdP; bid membership and roles live in the app database. |
| Observability | OpenTelemetry traces, `structlog` JSON logs | |
| Tests | `pytest` (+ Hypothesis property tests for geometry, units and money; Testcontainers for isolated database tests), Vitest, Playwright | |
| Dev environment | VS Code Dev Container (Linux), built on the same base image as production workers | `make`, `uv`, Node and CLI tools run inside the container; the host needs only Docker and VS Code |
| Dependency upkeep | `uv.lock`, `package-lock.json`, Renovate | Automated update PRs, gated by CI |
| Infra | docker-compose (dev); Terraform, Singapore cloud region (provider per ADR-004/ADR-008, decision D2) | |

### Repository layout

```
.devcontainer/           Dev Container definition
backend/config/          llm.yaml (+ llm.<env>.yaml overlays)
backend/src/firebid/
  api/  domain/  db/  storage/  jobs/  sandbox/  ai_gateway/{providers,prompts}/  agents/  evals/
  ingestion/  drawings/  specs/  qto/  boq/  pricing/  labour/
  clarifications/  risk/  package/  compliance/  coordination/
backend/tests/          frontend/src/          eval/ (manifests, templates; data lives outside git)
infra/                  scripts/               docs/{requirements,plan,adr}/
```

Create a module when its step needs it, following this layout.

## Conventions

- **Requirement traceability:** every test that verifies a requirement carries its ID: `@pytest.mark.req("FR-QTO-08")` in Python and `// req: FR-REV-04` above the test in TypeScript. `make req-coverage` reports which IDs have tests.
- **Units:** lengths stored as integer millimetres and displayed in metres (3 dp). Sheet coordinates are paper millimetres; model coordinates = sheet × verified scale.
- **Money:** `Decimal`, SGD, 2 dp, rounded half-up at line level. GST is computed separately at the configured rate.
- **IDs:** UUID primary keys plus human IDs per bid (`BID-2026-014`, `QTO-000347`, `CLR-0012`).
- **Immutability:** verified, baselined and approved records are versioned. A change writes a new version with `supersedes_id` and leaves the old one intact.
- **Audit:** every state transition, gate decision and human edit writes one append-only audit event (who, when, before, after, reason). Tamper evidence is a hash chain **per bid**, with one chain link per database transaction, so bids never wait on each other.
- **Jobs:** background work is queued in the same database transaction as the data it processes, so a rollback leaves no orphan job. Delivery is at-least-once, so every job is idempotent.
- **Stage caching:** each pipeline stage stores its output keyed by a fingerprint of its inputs and its code version. Re-runs (e.g. after an addendum) skip sheets whose fingerprint is unchanged.
- **Isolation:** bid-scoped tables enforce membership twice: in the application's query layer and with PostgreSQL row-level security.
- **State machines:** requirements §7 models live in `domain/` as explicit transition tables with guards. Services call them; nothing sets a state field directly.
- **Configuration over constants:** statutory and commercial values (GST rate, levies, FX buffers, thresholds, revision ordering schemes) are configuration with an effective date.
- **Singapore terminology:** rising main, breeching inlet, landing valve, hose reel, installation control valve set, subsidiary valve (requirements §2.5).

## LLM providers and AI usage

The platform uses multiple LLM providers, chosen per task by configuration. Calling code names a **route** (a task such as `title_block_read` or `boq_mapping`) and never names a provider or model.

- **Gateway and adapters.** All model calls go through `ai_gateway`, which exposes one provider-neutral interface: messages with text, image and document parts; tool calls; structured output to a Pydantic model; streaming; and batch submission. Each provider is an adapter in `ai_gateway/providers/` built on that provider's official SDK. The adapters shipped are:
  - **Anthropic:** the Claude API, plus Claude through Amazon Bedrock, Google Vertex AI or Microsoft Foundry via the Anthropic SDK's platform clients.
  - **OpenAI:** the OpenAI API and Azure OpenAI.
  - **Google:** Gemini via the Gemini API or Vertex AI.
  - **OpenAI-compatible:** endpoints serving self-hosted open-weight models.

  Adding a provider means a new adapter, the shared adapter contract tests, and a config entry. Calling code does not change.
- **Configuration** (`backend/config/llm.yaml`, with per-environment overlays) is schema-validated and versioned by content hash, and holds secrets by reference only:
  ```yaml
  providers:   # endpoint/region, credential ref, retention and no-training terms, approved data classes, enabled
    anthropic: {region: <per ADR-004>, credentials: secret://llm/anthropic, approved_data_classes: [internal, confidential, commercial]}
  models:      # provider, model ID, capabilities, context/output limits, effective-dated prices
    claude-opus-5: {provider: anthropic, capabilities: [vision, pdf_input, structured_output, citations, caching, batch]}
  routes:      # required capabilities, data class, primary + ordered fallbacks, reasoning level, limits
    title_block_read: {requires: [vision, structured_output], data_class: confidential, models: [claude-opus-5, <fallback>], reasoning: low}
  ```
  Startup fails when a route's model lacks a capability the route requires, or when any model in its chain belongs to a provider not approved for the route's data class.
- **Data classes.** `internal`, `confidential` (tender documents, drawings, quantities), `commercial` (prices, quotations, margins) and `personal` (PDPA). Each route declares the highest class it sends. The gateway sends a call only to providers approved for that class, on every attempt including fallbacks. When no approved model is available, the call escalates to a human task.
- **Defaults and changes.** At launch every route's primary model is Anthropic `claude-opus-5` with adaptive thinking. Model IDs for other providers are entered in configuration from each provider's current documentation. A route's model chain or reasoning level changes only when `firebid-eval compare-models` on the golden set shows equal or better quality (with cost and latency recorded) and the product owner approves.
- **Portable patterns.**
  - Routes set `reasoning: low | medium | high`, and each adapter maps it to its provider's parameter (for Anthropic: `effort`).
  - Structured outputs are validated against the Pydantic model by the gateway whatever the provider.
  - Where a provider lacks a capability, the adapter emulates it: PDF pages rendered to images, structured output through a tool call, sequential calls in place of a batch API. The caching hint is ignored where unsupported.
  - Citations are provider-neutral. The model returns source IDs with quoted spans, and a deterministic verifier checks each span against the source text. A provider's native citations may be used, and are verified the same way.
- **Embeddings** (Phase 3 retrieval) use separate embedding routes with their own provider choice. Every stored vector records its embedding model and version, and changing the embedding model triggers a re-index.
- **Resilience.**
  - Retryable errors, rate limits and open circuit breakers move the call to the next model in the route's chain.
  - A refusal, or a schema-validation failure after one retry, escalates to a human task.
- **Efficiency.**
  - Responses are cached, keyed by route, model, prompt version and input fingerprint, so re-processing identical input makes no new call. A user's explicit "regenerate" bypasses the cache.
  - A rate limiter shared across workers (tokens and requests per minute, per provider and model) keeps parallel jobs inside provider limits.
  - Bulk, non-urgent work uses the provider's batch API where available.
- **Debugging payloads.** Prompts and responses stay out of logs and traces. For diagnosis, an opt-in payload store keeps them per bid: encrypted, short retention, readable only by named administrators, with every read audited.
- **Prompts** live in `ai_gateway/prompts/<route>/v<N>.md`, with optional model-family variants `v<N>.<family>.md`, versioned by content hash. Every call records route, provider, model, prompt version, config version, tokens, cost, latency and bid ID.
- **SDK usage.** When writing or changing an adapter, follow that provider's official SDK documentation; for the Anthropic adapter, load the `claude-api` skill if your environment has it.
- **Tests.** CI uses a fake adapter and recorded fixtures. Every real adapter passes the shared contract test suite. Live tests carry `@pytest.mark.live` plus the provider name, and run only when that provider's credentials are present.

## Guardrails (hard rules)

1. Every quantity comes from geometry, extracted text or a versioned rule, and carries an evidence record. A BOQ line without trace is marked provisional or lump sum, or it blocks the gate.
2. Model and rule outputs are proposals. Only a person's action moves them to Verified or Approved, and only through the domain state machines.
3. Arithmetic (lengths, counts, conversions, prices, totals) runs in deterministic, unit-tested Python. Model output supplies labels, mappings and text, never final numbers.
4. Every price links to a source record (rate library entry, quotation, PO) with date and validity. A line with no sourced price shows "unpriced".
5. Every compliance statement carries a source-type label and a clause citation. A statement without a retrieved citation is labelled "AI recommendation".
6. Only sheets in the Current revision state feed takeoff.
7. Content reaches clients or authorities only by a named user's explicit action (download or export). The platform has no integration that submits to SCDF, CORENET X or any authority.
8. Tender documents, quantities and prices are confidential. Every query is scoped by bid membership, in application code and by PostgreSQL row-level security. Logs and traces carry IDs and metrics, not document text or prices. Data reaches an LLM provider only when that provider is approved for the call's data class (region, retention and no-training terms per ADR-004). The gateway enforces this on every call and fallback.
9. Files from outside the company (tender sets, quotations, models) are opened only by sandboxed parser workers, after the malware scan.

## Definition of done (every step)

- `make lint`, `make typecheck` and `make test` pass locally and in CI. Migrations upgrade and downgrade cleanly.
- Every requirement ID in the step's scope has at least one test tagged with it. Each acceptance criterion stated in the requirements is automated, or listed in the build log as a manual check with its reason.
- From step P0-05 onward, `firebid-eval` shows no regression beyond tolerance on affected metrics.
- OpenAPI schema, README sections and runbooks reflect the change.
- One entry is appended to `docs/plan/BUILD_LOG.md` using the template in that file.
