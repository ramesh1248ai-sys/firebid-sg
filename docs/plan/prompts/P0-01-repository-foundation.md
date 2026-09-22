# P0-01 · Repository Foundation and ADRs

**Builds on:** the current folder, which holds only requirements and plan documents. **Needs:** Docker running on the host; a GitHub repository for CI (ask before creating one).

Begin by replying with a plan that maps each item under **Done when** to the work that satisfies it. Wait for approval before changing files.

## Read first

- `docs/plan/project-context.md`: binding architecture, layout, conventions and guardrails.
- `docs/plan/IMPLEMENTATION_PLAN.md` §1–§4: how steps, prompts and the build log fit together.
- `docs/requirements/FireBid_SG_Requirements_v2.md` §10 (NFRs), §12 (technology pattern), §15.3 (dependencies).

## Goal

Create a runnable monorepo skeleton that every later step builds on:
- backend and frontend apps and a Linux Dev Container;
- a local stack in docker-compose, including a PostgreSQL-backed job queue;
- CI, requirement-traceability tooling and the build log;
- the first architecture decision records.

## Build

1. **Git:** initialise with `main` as the default branch and a `.gitignore` covering Python, Node, `.env*`, local data and `eval/data/`.
2. **Dev Container** (`.devcontainer/`): a Dockerfile on the same Debian/Python 3.12 base image the production workers will use, with:
   - `make`, `uv`, Node LTS and the Playwright browser dependencies;
   - the Docker CLI, so `make up` works from inside the container.

   Add `devcontainer.json` with the recommended VS Code extensions. The Windows host needs only Docker Desktop and VS Code, and every `make` target runs inside the container.
3. **Backend** (`backend/`): a `uv` project for package `firebid`, pinned to Python 3.12. It has:
   - a FastAPI app exposing `/health` (checks database and job-queue connectivity) and `/version`;
   - settings via `pydantic-settings`, with a `.env.example` listing credential variables for each LLM provider, all empty;
   - `structlog` JSON logging with a request-ID middleware;
   - `ruff`, `mypy` and `pytest` with Hypothesis available.

   Register a `req` pytest marker that takes a requirement ID, and add one example test using it.
4. **Job queue:** add Procrastinate on the PostgreSQL database, with a worker entry point (`python -m firebid.jobs.worker`). Include one example job and one periodic heartbeat job whose last run is visible in `/health`. Workers run as separate processes. Redis is not part of the stack.
5. **Frontend** (`frontend/`):
   - Vite + React 19 + TypeScript, React Router, TanStack Query, Tailwind and shadcn/ui.
   - An app shell with a placeholder sign-in route.
   - An API client generated from the backend's OpenAPI schema, with a `make api-client` target.
   - Vitest and a Playwright smoke test that loads the shell.
6. **Local stack** (`infra/docker-compose.yml`), each service with a health check:
   - PostgreSQL 17 with pgvector;
   - MinIO, with a bucket created on start;
   - Keycloak with a dev realm, a test user for each role in requirements §3, and token claims mapped to Microsoft Entra ID's shape (`oid`, `preferred_username`, `roles`);
   - the backend, one job worker and the frontend.

   Add a root `Makefile` with `up`, `down`, `lint`, `typecheck`, `test`, `e2e`, `api-client` and `req-coverage`.
7. **Traceability** (`scripts/req_coverage.py`):
   - Parse every `FR-…` and `NFR-…` ID from the requirements Markdown.
   - Collect test tags from pytest `req` markers and `// req:` comments in frontend tests.
   - Print a coverage table (ID, priority, phase, tests), with a filter by phase and one by a list of IDs.
   - Exit non-zero when `--require` IDs are uncovered.
   - `make req-coverage` passes through `PHASE=` and `IDS=` variables, e.g. `make req-coverage PHASE=P1`. Later steps' prompts use that form.
8. **CI** (GitHub Actions), on every push:
   - lint, typecheck, backend tests, frontend tests and the Playwright smoke test;
   - a check that fails when the generated API client differs from the committed one;
   - the req-coverage report uploaded as an artifact.

   Add a Renovate configuration for grouped, CI-gated dependency update PRs.
9. **Build log:** create `docs/plan/BUILD_LOG.md` with this entry template, then add the first entry for this step:
   ```
   ## <Step ID> · <Step name> · <YYYY-MM-DD>
   - Summary:
   - Key modules / files:
   - How to run and demo:
   - Requirement IDs covered (test names):
   - Deviations and decisions (ADR links):
   - Manual checks and results:
   - Known gaps and follow-ups:
   ```
10. **ADRs:** create `docs/adr/` with a template and ADR-001 to ADR-007, each with status **Proposed**, context, options, recommendation and consequences:
    - **ADR-001 Stack and repository layout.** As in project-context, including the Dev Container and synchronous database access.
    - **ADR-002 PDF libraries and licensing.** Recommend `pypdfium2` for rendering and for vector and text extraction through the PDFium page-object API, with `pdfplumber` as fallback. Record that the P1-03 benchmark on real dense sheets, including PyMuPDF, decides whether a PyMuPDF commercial licence is worth buying.
    - **ADR-003 DWG-to-DXF conversion.** ODA File Converter behind an interface, run in the parser sandbox; licence check; fallback: request DXF from consultants.
    - **ADR-004 LLM provider strategy and data residency.**
      - Options: thin in-house adapters over each provider's official SDK behind a provider-neutral gateway; a third-party multi-provider library; a single provider.
      - Recommend in-house adapters. They keep full access to provider features (structured outputs, documents, caching, batches, citations) and keep data-class enforcement under our control.
      - List the candidate providers and access paths: Anthropic (direct, or through Bedrock, Vertex AI or Foundry), OpenAI / Azure OpenAI, Google Gemini / Vertex AI, and self-hosted models.
      - Include a table of region, retention, no-training terms and approved data classes per provider.
      - Pending sponsor decision D2.
    - **ADR-005 Drawing viewer.** Recommend OpenSeadragon deep-zoom tiles (low levels pre-rendered, close-up tiles rendered on demand and cached, WebP) with a Canvas/WebGL overlay and spatial index. The alternative is PDF.js vector rendering.
    - **ADR-006 Background jobs.** Options: a PostgreSQL-backed queue (Procrastinate), Celery with Redis or RabbitMQ, Temporal. Recommend Procrastinate, for transactional enqueue, one fewer data store to run and back up, and job history in SQL. Record the evidence from this step's example jobs.
    - **ADR-007 Agent execution and orchestration.** Recommend plain typed agent functions on the gateway, run as queued jobs, with human waits via HumanTask and the domain state machines. LangGraph is deferred; the Phase 4 orchestration engine (LangGraph vs Temporal vs the existing state machines) is re-decided at the Phase 3 exit.
11. **CLAUDE.md** at the repository root, at most 15 lines. It says to work inside the Dev Container, and points to:
    - `docs/plan/project-context.md`;
    - the requirements Markdown;
    - `docs/plan/BUILD_LOG.md`;
    - the `make` targets.

    The conventions stay in project-context, so this file holds pointers only.

## Done when

- Inside the Dev Container, `make up` starts every service and each reports healthy. `/health` returns 200 through the stack and shows the heartbeat job's last run.
- An example job queued from the API runs on the worker, and its result is visible in the job history.
- `make lint`, `make typecheck`, `make test` and `make e2e` pass inside the Dev Container, and the CI workflow passes on the first push.
- `make req-coverage` lists all 104 FR IDs and 15 NFR IDs with priority and phase, and shows the example test's tag as covered.
- `make api-client` regenerates the frontend client, and CI fails when the committed client is stale.
- ADR-001 to ADR-007 exist with status Proposed and a recommendation each, and the Renovate configuration is committed.
- `CLAUDE.md` and `BUILD_LOG.md` exist; the build log has the P0-01 entry.
