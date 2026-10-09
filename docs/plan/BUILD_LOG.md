# FireBid SG: Build Log

One entry per build step, newest last. Each step's agent reads this before planning, and appends its own entry when done.

Entry template:

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

---

## P0-01 · Repository Foundation and ADRs · 2026-09-22

- **Summary:** runnable monorepo skeleton. It has:
  - a Linux Dev Container on the production base image;
  - a FastAPI backend with JSON logging, request IDs and health checks;
  - a PostgreSQL-backed job queue with transactional queuing, a worker, and a heartbeat and example job;
  - Alembic migrations;
  - a React 19 shell with a generated, typed API client;
  - a docker-compose stack (PostgreSQL 17 + pgvector, SeaweedFS for S3, Keycloak with an Entra-shaped dev realm, migrate, backend, worker, nginx frontend);
  - a `Makefile`, requirement-coverage tooling, CI, Renovate, ADR-001 to ADR-007, and `CLAUDE.md`.
- **Key modules / files:**
  - `backend/src/firebid/`: `api/app.py` (factory, `/health`, `/health/live`, `/version`), `api/middleware.py` (request IDs), `api/checks.py`, `api/dev_jobs.py` (dev only), `jobs/` (`app`, `enqueue`, `tasks`, `worker`, `healthcheck`), `db/` (engine, base, system tables, `migrations/`), `logging.py`, `settings.py`, `api/export_openapi.py`.
  - `frontend/src/`: `app/` (shell, routes), `pages/`, `api/` (`openapi.json`, generated `schema.d.ts`, `client.ts`), `components/ui/button.tsx`; `e2e/smoke.spec.ts`.
  - `infra/docker-compose.yml`, `infra/keycloak/`, `infra/seaweedfs/`; `backend/Dockerfile`, `frontend/Dockerfile`, `frontend/nginx.conf`.
  - `Makefile`, `scripts/req_coverage.py`, `.github/workflows/ci.yml`, `renovate.json`, `.devcontainer/`, `docs/adr/`.
- **How to run and demo:**
  1. Open the repository in VS Code and choose "Reopen in Container". Setup runs `make bootstrap`.
  2. Run `make up`: all services start and turn healthy. The app is at http://localhost:8080 and the API at http://localhost:8000 (from inside the container, `host.docker.internal`).
  3. Run `make test-integration` and `make e2e`.
  4. To queue the example job: `POST /dev/jobs/example {"a":20,"b":22}`, then `GET /dev/jobs/{id}` shows `succeeded` and `{"sum": 42}`.
  5. Dev sign-in users: `<role>@firebid.test` with password `firebid-dev`, e.g. `estimator@firebid.test`. These are development only.
- **Requirement IDs covered (test names):** NFR-14, by `backend/tests/test_system_endpoints.py::test_every_response_carries_a_request_id_that_is_logged` and `::test_valid_inbound_request_id_is_kept_and_invalid_one_replaced`. P0-01 is an enabling step; it owns no other requirement IDs.
- **Deviations and decisions:**
  - **SeaweedFS instead of MinIO** for local S3. `minio/minio` is no longer published on Docker Hub, and the last Quay build is from September 2025. Recorded in ADR-001; `project-context.md` updated.
  - **Transactional queuing without an outbox.** Procrastinate 3.9 supports queuing on a caller-supplied connection, and this was verified against PostgreSQL 17 (ADR-006).
  - **Alembic owns the queue schema.** Revision `0001` installs Procrastinate's schema plus the system tables, and downgrades cleanly. Upgrading Procrastinate needs a new revision (ADR-006).
  - **Worker concurrency 1 per process;** scale by processes. Procrastinate advises against in-process concurrency for blocking synchronous tasks.
  - **Health endpoints:** `/health` is readiness and includes heartbeat freshness; `/health/live` is liveness for container checks. The worker's container check equals heartbeat freshness.
  - **Frontend tooling:**
    - The template ships **TypeScript 6** and **oxlint**; both kept.
    - `openapi-typescript` declares TypeScript 5 as its peer, so an npm override points it at TypeScript 6. Generation was verified.
    - shadcn/ui was set up by hand in its "new-york" (Radix) style, because the shadcn CLI is blocked by npm 11's `--allow-scripts` policy.
  - **Test HTTP client:** `httpx2` replaces `httpx`; Starlette's test client now requires it.
  - **Ports:** PostgreSQL is published on host port **55432**, because 5432 is taken on this machine; Keycloak is on 8081. All ports can be overridden with `FIREBID_*_PORT` variables.
  - **Tailwind theme:** a brand colour token (`--brand`, fire-protection red) was added.
- **Manual checks and results (Done when):**
  1. **Stack starts healthy:** ✅ Inside the Dev Container, `make up` brings all 6 services to healthy, and `/health` returns 200 with a heartbeat 12 seconds old.
  2. **Example job:** ✅ A job queued through the API runs on the worker, and its `succeeded` status and result `{"sum": 42}` appear in the job history (integration test).
  3. **Checks and CI:**
     - ✅ Inside the Dev Container (Linux, Python 3.12.14, GNU Make 4.3), `make lint`, `make typecheck` and `make test` pass (13 backend and 3 frontend tests). `make e2e` passes 2 tests, and `make test-integration` passes 6.
     - ❌ **CI did not pass on the first push.** GNU make reported "missing separator", because a generated `Makefile` recipe line was split in two. It couldn't be seen on the Windows host, which has no `make`.
     - ✅ Fixed in `4ee2449`; the next run passed both jobs (checks; stack + integration + e2e).
  4. **Requirement coverage:** ✅ `make req-coverage` lists 104 FR and 15 NFR IDs with priority and phase, and shows NFR-14 covered by the example tests.
  5. **API client:** ✅ `make api-client` regenerates the client with no drift inside Linux. With a deliberately stale client, CI's drift check exits 1.
  6. **ADRs and Renovate:** ✅ ADR-001 to ADR-007 exist, each Proposed with a recommendation, and `renovate.json` is committed.
  7. **Docs:** ✅ `CLAUDE.md` (13 lines) and this build log exist.
- **Known gaps and follow-ups:**
  - Renovate only runs once the Renovate GitHub App is installed on `ramesh1248ai-sys/firebid-sg`. That's a repository-admin action.
  - Protect `main` so CI must pass before merge. Also a repository-admin action.
  - Business track: decision D2 (provider data terms, for ADR-004), the ODA converter licence (ADR-003), and golden set collection are still open.
  - The worker reports unhealthy for up to about a minute after starting, until the first heartbeat runs (container `start_period` is 120 s).
  - Development job endpoints (`/dev/jobs`) are unauthenticated and mounted only in dev and test. P0-03 adds authorised job views.
  - The `/workspaces` bind mount needs git's `safe.directory`; `devcontainer.json` sets it on start.

---

## P0-02 · Domain Model, State Machines and Audit Log · 2026-09-22

- **Summary:** the persistence and domain core for Phases 0–1:
  - value types (`LengthMm`, `Money`, `Confidence`), lineage (`SourceRef`) and the full Appendix B evidence record;
  - the three Phase 1 state machines with role permissions and guards;
  - every Phase 0–1 entity, partitioned where it will grow;
  - an append-only, tamper-evident audit trail with a per-bid hash chain;
  - the audit API, object storage, human IDs and the personal-data inventory.
- **Key modules / files:**
  - `domain/`: `values.py`, `evidence.py`, `actors.py`, `state_machines.py`.
  - `db/`: `models/` (core, documents, takeoff, commercial, workflow, audit), `audit.py`, `ids.py`, `types.py`, `mixins.py`, `migrations/versions/0002_domain_model_and_audit.py`.
  - `services/transitions.py`; `api/audit.py`, `api/deps.py`; `storage/object_store.py`.
  - `scripts/data_inventory.py` → `docs/data-inventory.md`; `tests/db/` (conftest with Testcontainers, factories).
- **How to run and demo:**
  1. `make test` starts a PostgreSQL 17 container, migrates it and runs everything.
  2. `make up`, then `GET /audit?bid_id=…` for the history, `GET /audit/export.csv` for the CSV, and `GET /audit/chain/{bid_id}` for the chain status.
  3. `make data-inventory` regenerates the PDPA inventory; `make req-coverage IDS=FR-ADM-04` shows its tests.
- **Requirement IDs covered (test names):**
  - FR-DOC-07: `test_source_ref_round_trips_with_every_lineage_field`.
  - FR-ADM-04: `test_audit_api.py` (filters, paging, CSV export, chain status).
  - NFR-07: `test_personal_data_inventory_is_generated_and_current`.
  - NFR-09: `test_audit_chain.py` (append-only, tamper evidence, links per transaction, concurrency).
- **Deviations and decisions:**
  - **Composite keys on partitioned tables.** PostgreSQL requires the partition key inside the primary key, so `qto_item`, `detected_object` and `evidence` use `(id, bid_id)`, and references to them are two-column foreign keys. Partitioning now avoids migrating large tables later.
  - **A second database role.** The application connects as `firebid_app`, which has no UPDATE or DELETE on the audit tables and no `BYPASSRLS`; migrations run as the owner and create the role. P0-03's row-level security builds on this. The password comes from `FIREBID_APP_DB_PASSWORD`.
  - **One chain link per transaction per bid,** rather than per event, taken under a per-bid advisory lock. A 5,000-item bulk action costs one link.
  - **Alembic owns the queue schema too,** and ignores Procrastinate's tables during autogenerate (ADR-006).
  - **Tests need Docker** (Testcontainers). The Dev Container sets `TESTCONTAINERS_HOST_OVERRIDE`.
  - **The clarification and external-approval state models are not built yet;** they arrive with P2-06 and later, as the prompt allows.
- **Manual checks and results (Done when):**
  1. **Migrations:** ✅ `upgrade head`, `downgrade base` (zero leftover tables, functions or roles) and re-upgrade all succeed on a clean database. The suite runs against a migrated database.
  2. **State machines:** ✅ 284 table-driven tests cover every state pair, role refusal and guard; database tests confirm exactly one audit event per successful transition, and none for a refused one.
  3. **Append-only and tamper evidence:** ✅ The app role is refused UPDATE and DELETE; the trigger refuses even the owner; `verify_chain()` detects an altered row and a deleted row (tests tagged NFR-09).
  4. **Concurrency:** ✅ With one bid's chain lock held, a write to another bid completes in well under a second, while a second write to the same bid times out waiting, proving the lock is per bid. A 5,000-item bulk transition appends exactly one link.
  5. **Transactional queuing:** ✅ Covered by the P0-01 integration tests (rollback leaves no job; commit runs it). The helper now carries an idempotency-key convention.
  6. **Partitions:** ✅ Rows insert across several bids and months; `ensure_audit_event_partition` creates a future month on demand, and a daily job calls it three months ahead.
  7. **Property tests:** ✅ Hypothesis covers `LengthMm` and `Money` round-trips, associativity and rounding.
  8. **Lineage:** ✅ `SourceRef` round-trips with every field, including the region box (FR-DOC-07).
  9. **Audit API:** ✅ Every filter, keyset paging, a capped page size, a refused bad cursor, and the CSV export.
  10. **Data inventory:** ✅ Generated from column metadata; 7 columns are marked, and the test fails if the file drifts.
  11. **CI:** ✅ Green on both jobs, including the database tests on the runner.
- **Known gaps and follow-ups:**
  - The audit API has no authorisation yet; P0-03 scopes it to bid members and adds row-level security using the `bid_id` columns added here.
  - `Money` is stored as `numeric(14,2)`; multi-currency (FR-CST-04) needs a currency column on priced tables in P2-04.
  - Tamper-evidence tests disable the trigger as owner to simulate an attacker; production owner access should be restricted to deployment credentials (part of P1-11 hardening).
  - Partition counts (8 hash partitions) are a starting point, to revisit with real volumes at P1-11.
  - The `clean_database` fixture truncates between tests, so a very large future suite may want per-test schemas instead.

---

## P0-03 · Identity, RBAC and Bid Workspace · 2026-09-23

- **Summary:** the platform now has people in it. Someone signs in through the organisation's identity provider, is provisioned on first sign-in, and sees only the bids they are on — enforced at the API and again by the database. A bid manager can register a bid, the mandatory details are required before qualification can start, deadlines warn the people who own open work, and the history is readable and exportable from the browser. A security baseline mapped to OWASP ASVS L2 covers headers, CORS, body limits and rate limiting.
- **Key modules / files:**
  - `backend/src/firebid/auth/`: `claims.py` (Entra-shaped claim mapping), `tokens.py` (JWKS verification), `permissions.py` (the §3.2 RACI as a matrix, gate approvers), `provisioning.py` (first sign-in creates the person).
  - `backend/src/firebid/api/`: `deps.py` (principal, bid context, permission dependency), `bids.py` (workspace routes), `security.py` (headers, body limits, rate limiting), `audit.py` (now scoped to the caller).
  - `backend/src/firebid/services/`: `bids.py` (creation, mandatory details, dashboard), `alerts.py` (deadline alerts); `firebid/notifications.py` (console and email adapters).
  - `backend/src/firebid/db/`: `identity.py` (the acting user, per transaction); migrations `0003` (row-level security, service role), `0004` (bid stage), `0005` (deadline alerts), `0006` (policies on later partitions), `0007` (bid creation policies).
  - `frontend/src/`: `auth/` (PKCE sign-in, session, route guard), `pages/` (dashboard, bid form, bid detail, history, callback), `test/` (fake identity provider, harness); `e2e/bid-workspace.spec.ts`.
  - `docs/security-baseline.md`; `scripts/e2e.sh`.
- **How to run and demo:**
  1. `make up`, then open http://localhost:8080 and sign in as `bid.manager@firebid.test` / `firebid-dev`.
  2. "New bid" → fill in project, client, reference and submission deadline → the bid opens, and the dashboard shows it with days remaining and what is still missing.
  3. "History" shows `bid: created` with who did it; "Export CSV" downloads the filtered rows.
  4. Sign in as `estimator@firebid.test` in another browser profile: the dashboard is empty and the other bid's address reads "Bid not found".
  5. `make check` (lint, types, 407 backend + 13 frontend tests), `make test-integration` (10), `make e2e` (4).
- **Requirement IDs covered (test names):**
  - FR-BID-01 — `test_bid_workspace.py::test_a_bid_starts_registered_and_lists_nothing_missing`, `::test_work_cannot_start_while_details_are_missing`, `::test_work_starts_once_the_details_are_complete`.
  - FR-BID-02 — `test_bid_workspace.py::test_shows_stage_deadlines_and_task_counts`.
  - FR-BID-03 — `test_deadline_alerts.py::TestWhenAlertsFire`, `::TestWhoIsTold`, `::test_what_was_sent_is_recorded`.
  - FR-ADM-01 — `test_bid_workspace.py::test_each_gate_is_approvable_only_by_its_accountable_role`, `::test_creating_a_bid_needs_a_permitted_role`.
  - FR-ADM-04 (viewer) — `test_audit_api.py` (filters, paging, CSV, chain status, `::test_a_bid_the_caller_is_not_on_is_not_found`) and `App.test.tsx` ("the history").
  - NFR-06 — `test_security_baseline.py` (headers, CORS, body size, rate limit) and `test_claims_and_tokens.py` (expiry, audience, issuer, wrong key, unsigned, the verifier factory).
  - NFR-08 — `test_bid_workspace.py::test_every_bid_route_hides_the_bid_from_a_non_member`, `test_row_level_security.py` (application role, service role, every bid table, later partitions, creation).
- **Deviations and decisions:**
  - **`oidc-client-ts` rather than hand-written PKCE.** Sign-in is security-critical and the library handles PKCE, state, nonce and silent renewal. The token is kept in session storage only, so closing the tab ends the session.
  - **Keycloak needs an audience mapper.** Entra ID always issues `aud`; Keycloak does not unless told to. The dev realm now carries the mapper, so both providers look the same to the API.
  - **Row-level security is split by command on `bid`.** A single `FOR ALL` policy cannot express "anyone permitted may create a bid, only its team may read it", because the creator is not a member at the moment of the insert. Reading admits the creator as well as the team, because `INSERT ... RETURNING` reads the new row back. Migration `0007`.
  - **The in-process rate limiter is deliberate for now.** It holds while the API is one service; `docs/security-baseline.md` names the step that moves it out.
  - **Deadline alerts run on the service role**, because they legitimately cross bids. The service role has its own policy and still cannot rewrite history.
- **Manual checks and results:**
  - Signed in through Keycloak in a real browser and registered a bid: the token carries `aud: firebid-web`, and the API accepts it.
  - Confirmed the end-to-end run is what caught three defects that the unit tests could not (below).
  - `make check`, `make test-integration` and `make e2e` all green before pushing.
- **Defects found and fixed during the step:**
  - `get_token_verifier` was `lru_cache`d on a `Settings` object, which is not hashable: every authenticated request returned 500. The unit tests built verifiers directly and never called the factory. Now they do.
  - Creating a bid violated its own row-level security policy (above). The service-layer tests run as the database owner, where row-level security does not apply, so a test that creates a bid **as the application role** was added.
  - Alembic's `fileConfig` disabled every logger that already existed, so running migrations in-process silenced the application's own logging. Found by a test that only failed in a particular order; now covered directly by `test_migration_side_effects.py`.
  - The audit chain and transition tests had to be updated: the audit API now requires a signed-in caller, and the shared `bid` fixture is a complete, staffed bid.
- **Known gaps and follow-ups:**
  - **Multi-factor authentication** is the identity provider's responsibility (NFR-06) and is not configured in the dev realm. It must be required in the Entra ID tenant before production.
  - **TLS** is not in front of the local stack; the deployment step configures it (P1-10).
  - **The email notifier is unexercised in anger** — the tests use a recording adapter and the stack logs instead of sending. An SMTP host must be configured before alerts leave the building.
  - **Bid membership is managed by API only**; there is no screen for adding people to a bid yet.
  - **`FR-BID-04`** (several bids per project) remains Phase 2, as planned.
  - Repository admin: the Renovate GitHub App is installed on `ramesh1248ai-sys/firebid-sg` (2026-09-23), scoped to that repository alone. Mend's account default is Silent mode, which scans but never opens a pull request; this repository overrides it to Interactive, so onboarding and dependency-update PRs do arrive. The other repositories on the account keep the Silent default. **`main` cannot be protected server-side on this plan**: branch protection and rulesets both refuse a private repository on GitHub Free ("Upgrade to GitHub Pro or make this repository public"). A tracked `pre-push` hook (`.githooks/pre-push`, enabled by `make bootstrap`) stands in for it by refusing a direct push to `main`, so work goes through a branch and a pull request where CI runs both jobs. It is a local guard, not a control: `--no-verify` goes round it. Real enforcement needs GitHub Pro.

---

## P0-04 · Multi-Provider AI Gateway and Agent Runtime · 2026-09-23

- **Summary:** every model call in the platform now goes through one governed gateway. Calling code names a **route**; `backend/config/llm.yaml` decides which provider and model serves it, in what order, with what reasoning depth, and which data classes each provider may receive. Switching a route from Claude to Gemini, reordering fallbacks or widening a provider's approval is an edit to that file and a restart — no code change, no new build. Around that: adapters for Anthropic, OpenAI, Google and self-hosted models, a response cache and a shared rate limiter in PostgreSQL, cost metering with per-bid and per-run budgets, OpenTelemetry tracing with redaction, an opt-in encrypted payload store, an agent runtime with autonomy levels, and an admin view.
- **Key modules / files:**
  - `backend/src/firebid/ai_gateway/`: `types.py` (the provider-neutral vocabulary), `config.py` (`llm.yaml` schema, startup validation, config hash), `router.py` (chain resolution, retries, breaker, fallback, data-class enforcement), `breaker.py`, `cache.py`, `ratelimit.py`, `metering.py`, `prompts.py`, `emulation.py`, `tracing.py`, `payloads.py`, `providers/` (anthropic, openai, google, fake), `prompts/title_block_read/v1.md`.
  - `backend/src/firebid/agents/`: `base.py` (contract, autonomy levels, tool registry), `runtime.py` (idempotent runs, escalation to `HumanTask`), `title_block.py` (the demonstration agent).
  - `backend/src/firebid/redaction.py`; `backend/src/firebid/api/admin.py`; migrations `0008` (cache and rate buckets), `0009` (bid budget), `0010` (payload store).
  - `backend/config/llm.yaml` and `backend/config/README.md`; `frontend/src/pages/AdminPage.tsx`.
- **How to run and demo:**
  1. `make check` — lint, types, 577 backend tests and 15 frontend tests.
  2. Change a provider: edit `routes.title_block_read.models` in `backend/config/llm.yaml`, restart, and `curl localhost:8000/health` shows the new `config_version`.
  3. Sign in as `admin@firebid.test` and open **Platform**: routes with the model that would serve each, providers with their approved data classes and whether a key is configured, and spend by route, provider, model or bid.
  4. `uv run pytest -m live` — real calls to whichever providers have keys set. Costs money; never part of `make check`.
- **Requirement IDs covered (test names):**
  - FR-ADM-05 — `test_metering.py::TestPricing`, `::TestRecording`; `test_admin_api.py::TestCostView`.
  - NFR-05 — `test_config.py::TestStartupRefusesBadConfiguration`, `test_router.py::TestFallback`, `::TestCircuitBreaker`, `test_adapter_contract.py`, `test_prompts_and_emulation.py::TestEmulation`.
  - NFR-08 — `test_router.py::TestDataClassIsEnforcedOnEveryAttempt`.
  - NFR-11 — `test_router.py::TestSwitchingProviderIsConfigurationOnly`, `test_prompts_and_emulation.py::TestPromptRegistry`, `test_agent_runtime.py::TestRunningAnAgent`.
  - NFR-14 — `test_tracing_and_payloads.py::test_no_document_text_or_price_reaches_the_logs_or_the_spans`, `::TestRedaction`, `::TestPayloadStore`.
  - NFR-15 — `test_metering.py::TestBudgets`, `test_llm_cache_and_ratelimit.py::TestResponseCache`, `::TestSharedRateLimiter`.
  - FR-ADM-01 (agent autonomy) — `test_agent_runtime.py::TestAutonomyLevels`, `::TestEscalation`, `::TestCompletingATask`.
- **Deviations and decisions:**
  - **Data-class approval follows the product owner's direction, not ADR-004's recommendation.** All three providers are approved for `internal`, `confidential` and `commercial` ahead of decision D2. ADR-004 and `docs/decisions/D2-llm-provider-data-terms.md` record this rather than claiming an Anthropic-only launch the configuration contradicts.
  - **OpenAI and Gemini model IDs are unconfirmed.** They are marked in `llm.yaml`. `pytest -m live` is what catches a stale one; it needs keys and costs money, so it is not in CI.
  - **Structured output on OpenAI uses a JSON schema rather than an SDK `parse()` helper**, so the adapter does not depend on where that helper lives in a given SDK version. The gateway validates the result itself regardless of provider.
  - **The cache and rate limiter are in PostgreSQL, not process memory.** In process memory each worker believes it holds the whole budget; together they pass the provider's limit. A test races ten threads for five slots.
  - **Provider clients are built on first use.** A missing key should fail the route that needs it, where the router can fall back — not stop the gateway being constructed or break the admin page.
  - **Sub-parts E–J only.** Batch submission (`submit_batch`/`poll_batch`) is not built; `generate`, `stream` and `embed` are.
- **Manual checks and results:**
  - Confirmed `/health` reports `llm_routing` with the configuration version and enabled providers, and that a missing `llm.yaml` makes the service unhealthy rather than failing on first use.
  - Confirmed the admin page renders routes, providers and spend against the real stack.
- **Defects found and fixed during the step:**
  - The redaction allowlist was not one: a short string under an unknown key passed straight through, so a supplier's name or a sheet number would have been logged. Writing the NFR-14 test exposed it.
  - `PayloadStore.enable_for` wrote a row before checking the bid existed, so an unknown bid produced a foreign-key error rather than a clear refusal.
  - The row-level-security guardrail caught a speculative `bid_id` on the cache table that nothing wrote; removed rather than given a policy for a dead column.
  - The PDPA inventory guardrail caught `bid_budget.owner_email`; the inventory was regenerated.
  - The admin health endpoint constructed provider clients and so needed live API keys. Fixed by building clients lazily.
- **Known gaps and follow-ups:**
  - **Batch submission** is unbuilt. It matters for bulk sheet processing in Phase 1 and should be added before P1-03.
  - **Tracing is not exported anywhere.** Spans are produced and tested, but no OTLP exporter is configured; that belongs with the deployment step (P1-10).
  - **The circuit breaker is in process memory**, like the rate limiter was. It should move to PostgreSQL when the API runs as more than one service.
  - **`FIREBID_PAYLOAD_ENCRYPTION_KEY` is unset by default**, so the payload store cannot be used until someone sets one. That is deliberate, but it means the feature is inert until configured.
  - **The retention sweep for expired cache entries and payloads is not scheduled.** `delete_expired()` exists and is tested; a periodic job should call it (P3-05).
  - **`firebid-eval compare-models` does not exist yet** (P0-05), so the rule that a primary model changes only on evidence is a convention, not a gate.

---

## P0-05 · Evaluation Harness and Golden Set Tooling · 2026-09-23

- **Summary:** accuracy can now be measured the same way every time, and a route's model can be changed on evidence rather than opinion. The step delivers the golden-set format and the estimator workbook that unblock decision D3, the eight Phase 1 KPIs from §14, a seeded synthetic fixture generator so CI can test drawing logic without confidential tenders, a runner and report sliced by input class and consultant, a regression gate wired into CI, and `firebid-eval compare-models`.
- **Key modules / files:**
  - `backend/src/firebid/evals/`: `schema.py` (golden-set format), `template.py` (the estimator workbook), `importer.py` (validation with per-cell errors), `metrics.py` (§14 KPIs), `prediction.py` (the `Predictor` contract later steps implement), `synthetic.py` (fixtures), `dummy.py` (scriptable predictor), `runner.py` (suite, report, baselines, gate), `compare_models.py`, `cli.py`.
  - `eval/README.md`, `eval/baselines/synthetic.json`; `Makefile` targets `eval`, `eval-gate`, `golden-template`; CI job "Evaluation regression gate".
  - `docs/decisions/D3-golden-dataset.md`.
- **How to run and demo:**
  1. `make golden-template` → `eval/templates/golden_takeoff.xlsx`, the workbook for estimators.
  2. `cd backend && uv run firebid-eval import <filled.xlsx>` — reports every problem by tab, row and column; writes nothing until clean.
  3. `make eval` → `eval/results/synthetic.md`, all eight metrics sliced by input class and consultant.
  4. `make eval-gate` → passes; `uv run firebid-eval --root ../eval compare --count-error 0.25` → fails, naming the three metrics that regressed.
  5. `uv run firebid-eval --root ../eval compare-models --route title_block_read --models claude-opus-5,gpt-5.1` → side-by-side with cost and latency.
- **Requirement IDs covered (test names):**
  - FR-LRN-01 — `test_metrics.py` (46 hand-computed cases), `test_runner_and_gate.py::TestTheRegressionGate`, `::TestTheCommandLine::test_compare_fails_on_an_injected_regression`, `test_synthetic.py`, `test_golden_set_template.py`.
  - NFR-11 — `test_runner_and_gate.py::TestCompareModels` (ranking, data-class refusal, configuration version recorded).
- **Deviations and decisions:**
  - **Undefined is neither zero nor one.** Every §14 formula divides by a verified count, so a sheet with nothing to find has no score. Returning 1.0 would let empty sheets flatter the average and 0.0 would punish the platform for correctly finding nothing, so an undefined metric is `None`, excluded from aggregates, with the exclusions counted and reported.
  - **`pipe_length_error` is named as an error, not an accuracy**, because §14 defines it as a ratio where smaller is better. Calling it accuracy would have someone read 0.04 as a bad score.
  - **`false_detection_rate` is an offline approximation.** §14 defines it by what a person rejects, which needs the review workbench (P1-08). Offline it measures detections in excess of truth, and the docstring says so. `qto_effort` cannot be computed from drawings at all and is absent.
  - **Fixtures are generated from a seed, not committed.** The seed is the artefact; `eval/synthetic/` is git-ignored.
  - **The estimator workbook is generated too**, so a change to it is a reviewable diff rather than an opaque binary.
  - **`ezdxf[draw]` pulled in a ~200 MB Qt stack**; replaced with `ezdxf` plus `matplotlib`.
  - **Gate tolerances are not zero** (1–5% per metric). These are means over a small set, and a gate that fires on noise is a gate someone switches off.
- **Manual checks and results:**
  - Ran the gate both ways: clean predictor passes with exit 0; a 25% injected error fails with exit 1, naming `sprinkler_count_accuracy`, `missed_item_rate` and `calibration_error` with their tolerances.
  - Ran `compare-models` against the shipped `llm.yaml`: it ranked the candidates, showed cost and latency, and refused `text-embedding-3-large` with a readable reason, exiting 1.
  - Read the generated workbook tab by tab: Instructions first, hints under every header, the machinery tab hidden.
- **Defects found and fixed during the step:**
  - The dummy predictor truncated rather than rounded, so `count_error=0.001` dropped a whole sprinkler — a 2% error on a 48-head sheet and 16% on a six-head one. The knob did not mean what it said, and the tolerance test caught it.
  - `compare-models` printed raw enum reprs in its refusal message (`[<Capability.VISION: 'vision'>]`) instead of names.
  - `openpyxl` and `ezdxf` needed type stubs and a scoped `implicit_reexport` override; ezdxf is typed but declares no `__all__`, so strict mode rejected its own documented API.
- **Known gaps and follow-ups:**
  - **Nothing here measures real-world accuracy.** The synthetic set proves the pipeline, the arithmetic and the gate. Only decision D3's golden set can say whether the platform reads a real consultant's drawing, and every report says so in as many words.
  - **`compare-models` scores a dummy predictor.** Until P1-02 supplies one that actually calls a gateway route, it compares the harness rather than the models. The refusal and ranking logic is real; the numbers are not.
  - **The HTML report is not built** — Markdown and JSON only. The prompt allows either.
  - **The nightly job for the private golden set is not configured.** The CI job runs the synthetic suite only, which is correct; someone must schedule the real one once D3 delivers.
  - **`qto_effort` and the Phase 2–4 KPIs are out of scope** and remain unmeasured.

---

## P1-01 · Document Ingestion · 2026-09-23

- **Summary:** a tender set can be uploaded, scanned, read and opened. Every file ends in a state a person can read, every parser of an external file runs in a sandboxed process, each PDF page and CAD layout becomes a sheet with its paper size and lineage, and the low zoom levels of its tile pyramid are rendered at ingest so the viewer opens instantly. Close-up tiles are rendered on first request and cached. Built in six sub-parts; A and B were committed separately, C to F together.
- **Key modules / files:**
  - `backend/src/firebid/ingest/`: `detection.py` (content signatures, never the extension), `scanning.py` (clamd INSTREAM, an outage holds rather than passes), `archives.py` (bomb, traversal and nesting limits checked against declared sizes first).
  - `backend/src/firebid/sandbox/`: `limits.py` (the walls), `runner.py` (`run_sandboxed`), `safety.py` (`defusedxml`, Pillow pixel cap, readable failure reasons), `office.py` (LibreOffice for `.doc`/`.xls`), `probe.py` (which walls actually stood).
  - `backend/src/firebid/parsing/`: `pdf.py` (pypdfium2; crop-box sizes, vector/raster/mixed classification), `dxf.py` (ezdxf; paperspace layouts, page setup or extents).
  - `backend/src/firebid/imaging/`: `pyramid.py` (level maths, content-hash cache keys), `tiles.py` (lossless WebP).
  - `backend/src/firebid/services/`: `ingestion.py` (store, then scan, then register), `sheets.py` (document to sheets to tiles).
  - `backend/src/firebid/api/`: `documents.py` (upload, presigned, rescan), `sheets.py` (sheets, tiles, thumbnails), `progress.py` (counts and SSE).
  - `frontend/src/pages/DocumentsPage.tsx`, `SheetViewerPage.tsx` (OpenSeadragon).
  - `infra/sandbox/Dockerfile`, the `sandbox` and `clamav` services in `infra/docker-compose.yml`, migrations `0011` and `0012`.
  - `backend/src/firebid/evals/ingest_benchmark.py`, `Makefile` target `ingest-benchmark`.
- **How to run and demo:**
  1. `make up`, then open a bid and click **Tender documents**.
  2. Drop a zip of drawings in. The counts move as files are scanned and read; refusals appear with their reasons.
  3. Click a sheet thumbnail to open the viewer; zoom past the pre-rendered levels and close-up tiles fill in.
  4. `make ingest-benchmark SHEETS=300` writes `eval/results/ingest-throughput.md`.
  5. `make e2e` runs `frontend/e2e/document-ingestion.spec.ts`, which does all of the above against the real stack. Last run 2026-09-27: all 6 E2E tests pass.
- **Requirement IDs covered (test names):**
  - FR-DOC-01 — `tests/ingest/test_detection_and_archives.py::TestDetection`, `tests/db/test_document_ingestion.py`, `tests/db/test_document_api.py`, `tests/db/test_legacy_conversion.py`, `tests/parsing/test_pdf_and_dxf.py`, `tests/imaging/test_pyramid_and_tiles.py`, `tests/db/test_sheet_pipeline.py`, `tests/db/test_sheet_api.py`.
  - FR-DOC-07 — `test_sheet_pipeline.py::TestProcessingAPdf::test_a_sheet_carries_its_lineage`, `test_sheet_api.py::TestListingSheets::test_a_sheet_carries_its_lineage`.
  - NFR-06 — `tests/sandbox/test_parser_sandbox.py` (all), `test_detection_and_archives.py::TestArchiveLimits`, `test_document_ingestion.py` (EICAR, outage), `test_sheet_api.py::TestWhoMaySee`.
  - NFR-01 — the benchmark below.
- **Throughput (NFR-01):** measured in the sandbox image on 4 workers, 8 GB, against 300 generated A3 vector drawings.

  | Measure | Value |
  |---|---|
  | Time to all sheets viewable | 23.0 s |
  | Per sheet | 0.077 s (784/min) |
  | Median parse | 0.002 s |
  | Median render and tile | 0.284 s |
  | Peak memory per worker | 139.7 MB |
  | Tiles per sheet | 35.0 |
  | Storage per sheet | 16.2 kB |
  | Total tile storage | 4.9 MB |

  **Read this as a floor, not a promise.** The fixtures are clean A3 vector exports of a few hundred entities; a real A0 tender sheet with xrefs, hatching and an embedded scan is heavier by an order of magnitude or more. What the numbers do establish is that ingestion scales across processes, that memory per worker is bounded well inside the 4 GB container limit, and that storage is trivial — so the NFR-01 budget is effectively all still available to classification and takeoff. The figure to re-measure is this one, on the D3 golden set, once it exists.
- **Deviations and decisions:**
  - **The pool is not literally without a network, and the honest wall is the container's.** A queue-driven worker must reach PostgreSQL and object storage. Each parse child additionally asks the kernel for its own network namespace, but **Docker's default seccomp profile refuses `unshare`**, which was measured rather than assumed: a `ctypes` call to `connect(2)` from inside the sandbox reached the internet on a default-profile container. The wall that holds in every configuration is `internal: true` on the pool's Docker network — verified as "Network is unreachable" from it, against reachable from the default network. Python's socket layer is blocked in the child either way. `/health` reports which walls actually stood; `docs/security-baseline.md` records the gap and the two ways to close it (a vendored seccomp profile, or `seccomp=unconfined` with `cap_drop: ALL`). Open item 6 there tracks the decision, which belongs with deployment (P1-10).
  - **Only the low zoom levels are rendered at ingest** (ADR-005). Levels up to 2,048 px are cut from one render; the rest are rendered on first request. An estimator zooms into the riser shafts and the valve room, not every square metre of a car park, so rendering everything would spend most of ingest on tiles nobody opens.
  - **Tiles are keyed by content hash and shared across bids.** The same consultant's drawing on two bids renders once. That means the key cannot decide who may see it, so every tile route resolves the sheet through the bid first; `test_sheet_api.py::TestWhoMaySee` tries it the other way round.
  - **A page's size comes from its crop box, not its media box**, because consultants routinely export A1 content inside an A0 media box.
  - **Coverage decides the content class before object counts do.** A scan with a vector title block stamped on it is still a scan as far as measuring is concerned.
  - **A converted `.doc` or `.xls` is a derived document, and the original is kept.** A converter is a lossy step and the lineage has to lead past it. A conversion that fails does not fail the upload.
  - **`quality_band` and `manual_takeoff_recommended` are left null.** Sheet quality is P1-03's decision; this step records the object counts it will need in `quality_detail` rather than guessing.
  - **Sub-part scope:** the prompt's items 1-13 are all built except that DWG conversion remains unavailable pending ADR-003 — a DWG is refused with a message asking for the DXF export, which is the fallback the prompt names.
- **Manual checks and results:**
  - Ran a real `.xls` to `.xlsx` conversion through `convert_legacy` inside the built sandbox image: LibreOffice 7.4.7.2, 5,632 bytes in, 4,874 out, and the result detected as `xlsx`.
  - Ran the sandbox test suite on Linux in a container: 24/24 pass, including the memory and CPU limits that skip on Windows.
  - Measured the network walls directly, as described above.
  - Checked that a rendered page is not blank, and that a tile cut on the on-demand path is byte-identical to the same tile cut at ingest.
- **Defects found and fixed during the step:**
  - `put_once` raised `ObjectExists` where the code claimed a second write was a no-op, so a document row removed while its write-once object survived would have failed re-ingestion.
  - The upload report counted a converted copy as an uploaded file, so the totals stopped matching what the estimator sent.
  - The test harness's `fetch` stub only understood a `Request`, so any call passing a URL and an init — the upload and the SSE stream — silently returned 404.
  - `render_tile` referenced a `Sheet.kind_for_render` attribute that never existed; the document's kind is now passed in.
  - The sandbox image installed the project into the wrong virtualenv (`VIRTUAL_ENV` instead of `UV_PROJECT_ENVIRONMENT`), so `firebid` was absent at runtime.
  - The database test fixture cleared the cached engine but not the cached session factory, so `session_scope` could stay bound to the default URL from an earlier test. The progress stream was the first route under test to use it, and failed only in a full run. `clear_engine_caches()` now clears all four caches.
  - **Found by the first E2E run against the real stack**, none of which the unit tests could see:
    - `parse.document` ran with no acting user, so row-level security hid every document and each job logged `parse_skipped_missing_document`. Nothing ever became a sheet. The job now takes the uploader's id and runs `acting_as` them, as `db/identity.py` requires of every job.
    - The progress stream read on its own session, also with no acting user, so it saw an empty bid, sent one event and never updated or closed. It now acts as the caller.
    - Both escaped the unit tests because those connect as the table owner, which row-level security does not restrict. A shared `as_application_role` fixture now runs such code as the application role; `TestTheParseJob` and the stream test use it.
    - The sheet list refreshed only on stream events, never on the fallback polling; it now refetches when the ready count changes.
    - Thumbnails were plain `<img>` requests with no token, so every one returned 401. They are now fetched with the token.
    - Sheet cards and the viewer heading showed only "page 1" or "Model". The sheets API now returns the source `filename`, which the page shows.
    - The E2E EICAR fixture appended the test string to a PDF. Real ClamAV matches EICAR only at the start of a file, so it passed the scan (the unit test's fake scanner matched anywhere). The fixture is now a workbook carrying the file inside, which ClamAV flags, and the test asserts that row says Quarantined.
    - Also: the failures heading read "1 file need attention", and the spec had three ambiguous selectors.
  - **A file released by a rescan was never read.** `rescan` moved a held file to `received` but queued no parse job, and skipped the legacy conversion an upload would have done, so a PDF held during a ClamAV outage never became sheets. Rescan is now `Ingestor.release_held()`, which sends a released file down the rest of the upload path, and the API queues parsing for it as it does after an upload. Checked live: stopped ClamAV, uploaded a drawing (held), restarted ClamAV, rescanned; it became one sheet with a thumbnail, and a second rescan moved nothing.
- **Known gaps and follow-ups:**
  - **The per-job network namespace is inert under Docker's default seccomp profile.** Tracked as open item 6 in the security baseline; the decision belongs to P1-10.
  - **DWG is still unreadable** pending ADR-003. Every DWG is refused with a message naming the DXF export as the way forward.
  - **The benchmark measures synthetic A3 drawings.** Re-measure on the D3 golden set.
  - **No revision or sheet-number extraction yet** — `SheetRevision` is untouched here; that is P1-02.
  - **`tender_package_id` is accepted but never set by the API.** Packages arrive with P1-02's addenda handling.
  - **Failed parse jobs do not retry with backoff.** Procrastinate's retry strategy is not configured on `parse.document`; a failure lands in `rejected` with its reason instead, which is visible but final until the file is re-uploaded.
  - **The EICAR unit test's fake scanner matches the string anywhere**, which real ClamAV does not; it proves the pipeline's handling of a verdict, not what ClamAV will flag.

---

## P1-02 · Classification, Registers and Revision Control · 2026-09-27

- **Summary:** every drawing and document in a tender set is identified, classified and put in a register with exactly one Current revision each. Title blocks are read deterministically (text layer, DXF entities or OCR, then a remembered consultant layout), with the `title_block_reader` model as a fallback on a crop and a person after that. Revisions are ordered by a configurable scheme, sources that disagree send a revision to Conflict for a person, and only Current sheets are offered to takeoff. Addenda link the revisions they bring and answer "what changed". Every sheet carries an expected-accuracy band and, for poor scans, "manual takeoff recommended". Built in six sub-parts (A–F), each committed separately.
- **Key modules / files:**
  - `backend/src/firebid/drawings/`: `title_block.py` (locate, history table, label pairing, OCR digit fixes, remembered layouts), `revisions.py` (schemes, ordering, filename revisions, source reconciliation), `quality.py` (bands and the manual flag).
  - `backend/src/firebid/parsing/`: `text.py` (text spans from PDF and DXF, OCR of a region, title block crops), `digest.py` (opening of a PDF, DOCX or XLSX), `transmittal.py` (drawing lists).
  - `backend/src/firebid/ingest/`: `classification.py` (document type rules), `document_identity.py` (document number or normalised title, revision).
  - `backend/src/firebid/services/`: `title_blocks.py`, `classification.py`, `revisions.py` (settle, recompute Current, conflict, resolve, `current_sheets`), `registers.py`, `addenda.py` (`affected_items` with providers).
  - `backend/src/firebid/agents/doc_classifier.py` and route `doc_classify` with prompt `v1`; `run_agent_with_result` in the agent runtime.
  - `backend/src/firebid/api/`: `registers.py`, `addenda.py`; `documents.py` takes `addendum_id`.
  - `backend/src/firebid/evals/doc_classification.py`; metrics `drawing_number_accuracy` and `revision_accuracy`.
  - `backend/config/revisions.yaml`, `backend/config/input_quality.yaml`.
  - Migrations `0013`–`0016`; Tesseract in the sandbox image, the Dev Container and CI.
  - `frontend/src/pages/RegistersPage.tsx`; the addendum choice on the upload form; the quality note in the viewer.
- **How to run and demo:**
  1. `make up`, open a bid, **Tender documents**: upload a set (choose "A new addendum…" to upload one as an addendum).
  2. **Registers**: the drawing and specification registers, what blocks confirmation, the action on each blocking row, **Export to Excel**, and **Register confirmed** once nothing is undecided.
  3. `make eval-docs` writes `eval/results/doc_classification.md`.
- **Requirement IDs covered (test names):**
  - FR-DOC-02 — `tests/drawings/test_title_block.py` (all), `tests/db/test_title_block_service.py`, `tests/ingest/test_classification.py`, `tests/db/test_document_classification.py`, `tests/evals/test_doc_classification.py`, `test_register_api.py::TestDecisions::test_a_confirmed_reading_teaches_the_consultants_layout`.
  - FR-DOC-03 — `tests/db/test_register_api.py` (both registers and their filters, both exports reopened and checked column by column, confirmation refused and then accepted).
  - FR-DOC-04 — `tests/drawings/test_revisions.py` (with Hypothesis properties), `tests/db/test_revision_control.py` (sampled arrival orders of R01–R04, disagreeing sources in and out of Conflict, labels that cannot be ordered, transmittals either side of the drawings).
  - FR-DOC-05 — `tests/db/test_addenda.py`.
  - FR-DOC-06 — `tests/drawings/test_quality.py`, `test_title_block_service.py::TestInputQuality`, the viewer test in `Documents.test.tsx`.
- **Title block accuracy (FR-DOC-02, target ≥ 95% on vector title blocks):** measured with `firebid-eval run --suite doc_classification` in the sandbox image (Tesseract 5), four seeds of about ten sheets per form, deterministic reader only (no model):

  | Form | Drawing number | Revision | Number, revision and Current/Superseded |
  |---|---|---|---|
  | CAD (DXF) | 100% | 100% | 100% |
  | Vector PDF, text layer | 100% | 100% | 100% |
  | Vector PDF, outlined text (OCR) | 100% | 91–100% | 91–100% |
  | Scan, 150 dpi (OCR) | 100% | 82–100% | 73–100% |

  **Vector title blocks meet the target on every seed.** The OCR misses are all of one kind, `C` read as `G` or `1` as `L` in a revision, and **every one was flagged** (confidence 0.61–0.70, below the 0.85 threshold), so in production each goes to the model check and then to a person rather than into the register. None was silent. These are synthetic fixtures: the figures that matter come from the D3 golden set, which the suite uses automatically once it is imported.
- **Deviations and decisions:**
  - **Model calls happen on the ordinary worker, never in the sandbox pool.** The pool has no network, so it crops the title block to a PNG and queues `title_block.check`; the worker sends only the crop and the text. An unclear DXF goes straight to a person: CAD text is exact, and a model cannot read an entity better than ezdxf did.
  - **The platform registers a revision and makes it Current on its own when the evidence agrees**, as the P0-02 state machine allows (`SYSTEM` may register, supersede and flag a conflict). Only people resolve a conflict, restore or withdraw. The Estimator's "Register confirmed" is the human decision on the set, and it is refused while anything is in Conflict, unidentified, or of an unconfirmed type.
  - **When two revisions cannot be ordered, both go to Conflict, the Current one included.** Takeoff stops using that drawing until someone decides, rather than pricing what might be the superseded sheet.
  - **The tie-breaker is the addendum's date, then the transmittal's, and only then the title block's**, because consultants often leave the title block at the date of first issue.
  - **Transmittals are read from workbooks with drawing number and revision columns.** CSV is not ingested (P1-01 has no CSV kind). A transmittal re-checks revisions already registered, because files in one upload are read in any order.
  - **Document revisions share the sheet revision state machine** (requirements §7 names one model), keyed by document number or normalised title. A partial unique index enforces one Current per drawing and per document in the database as well as in the guard.
  - **A Received revision may be withdrawn by a person**, so a cover sheet nobody will identify does not block confirmation for ever.
  - **The REV cell is checked against the title block's own revision history.** An OCR value the history lacks, with a near variant the history has, takes the variant; otherwise it is capped below the threshold. A text-layer REV missing from its history is flagged, not changed.
  - **Revision schemes and quality thresholds are configuration** (`config/revisions.yaml`, `config/input_quality.yaml`), with effective dates.
  - **The synthetic fixtures were made realistic before they could measure anything:** a real title block with a revision history, printed on white (ezdxf drew colour 7 white on white), at true A3 scale (ezdxf shrank the page to a third, making 2.5 mm text less than 1 mm), with live text or outlines.
- **Manual checks and results:**
  - Ran the suite in the sandbox image over four seeds (results above), listed every OCR misread, and confirmed each was flagged rather than silent.
  - The exported registers are checked through openpyxl in the tests (headers, date cells, frozen panes, the About sheet). Opening a file in Excel itself is still to be done by a person.
- **Defects found and fixed during the step:**
  - The title block reader first took the REV cell for the history table's header, because the bottom row also holds a DATE cell. DATE alone is now weak evidence of a history table.
  - Fingerprints included the history table, which grows with each issue, so the same consultant's sheets did not match.
  - The history check first corrected a right revision to a wrong one: history labels had not been cleaned of OCR swaps the way the REV cell was. The eval found it (seed 1 fell from 100% to 70%); it is fixed and pinned by a test.
  - `AgentRun` keeps no output, so a successful model reading would have been lost. `run_agent_with_result` returns it.
  - `_issued` preferred the title block's date, so an addendum's date never broke a tie.
  - The golden set's files were to live in `eval/files/`, which git did not ignore. It does now.
  - Workbooks and Word files (from P1-01) were never processed, stayed `received` for good, and kept a set's progress from ever showing as finished. They are now read and classified.
  - **Found by PR #10's end-to-end run:** a job running in the worker could not queue another (the parse job queueing the title block check), because Procrastinate's connector is async inside the worker and `enqueue` handed it a synchronous connection. `enqueue` now always defers through a synchronous job manager on the caller's connection, pinned by `tests/db/test_enqueue.py`.
  - The sandbox's `/scratch` tmpfs was mounted as root, so its `HOME`, matplotlib's cache and LibreOffice had nowhere to write (a P1-01 defect, missed because P1-01 checked conversion with a plain `docker run`). It is now owned by the sandbox user.
- **Known gaps and follow-ups:**
  - **The `doc_classification` suite has no accepted baseline, so `make eval-gate` does not check it.** A named approver should accept one (`firebid-eval --root ../eval accept --suite doc_classification --approver "Name"`), and the suite should then be added to the gate.
  - **Clause-level addendum links wait for P1-06**; a revised specification is linked as a whole document.
  - **Opening an export in Excel by hand** is still to be done.
  - **The model checks are exercised with the fake adapter only.** A live run of `title_block_read` and `doc_classify` belongs with the credential-gated tests.
  - **No E2E test goes through the registers page yet.** The page has Vitest tests and the API has tests; an E2E path through upload, registers and confirmation should follow.

## P1-03 · Sheet Geometry, Views and Scale · 2026-09-27

- **Summary:** every vector sheet becomes one geometry model, whether it arrived as DXF or PDF. The model holds lines, polylines, arcs, circles, text, inserts, hatches and dimensions in sheet millimetres, and each primitive records how it was extracted. It is stored as Parquet, keyed by content hash and extractor version (the stage cache), and indexed by place in PostgreSQL. Scanned pages yield OCR words with confidences. Views, their stated scales and the structural grid are then found from the geometry. A scale counts as verified only when the drawing's own dimensions agree with it. Lengths are refused on any view that is not verified or calibrated, and a calibration names the person who made it. Built in parts A–E, committed separately.
- **Key modules / files:**
  - `backend/src/firebid/drawings/`:
    - `geometry.py`: the model, `Builder`, vectorised `segments()`, Parquet.
    - `stage_cache.py`.
    - `scale.py`: stated scales, evidence, verdicts, `measure`, `calibrated`.
    - `grids.py`: bubbles and lines, references, label-based grid coordinates, overlap.
    - `views.py`: view titles and kinds, extents, the title block left out, `analyse`.
  - `backend/src/firebid/parsing/`:
    - `geometry_dxf.py`: paperspace, viewports clipped and transformed, modelspace placed at its stated scale, inserts and dimensions kept and exploded.
    - `geometry_pdf.py`: pypdfium2 page objects with form recursion; pdfplumber fallback; OCR of scanned pages by word.
  - `backend/src/firebid/services/`:
    - `geometry.py`: extract, cache, index.
    - `views.py`: detect views after geometry; `measure`, `calibrate` and `locate`.
    - Both run in the parse job, after the title blocks.
  - `backend/src/firebid/api/views.py`:
    - `GET …/sheets/{id}/views`
    - `POST …/views/{id}/measure`: 409 unless measurable.
    - `POST …/views/{id}/calibrate`: needs `document.review`; audited.
    - `GET …/sheets/{id}/locate?x&y`
  - `backend/src/firebid/evals/pdf_benchmark.py`: `generate`, `run` and `throughput`.
  - ADR-002.
  - Migrations `0017` (`sheet_geometry`, and `geometry_feature` with a GiST `box` index) and `0018` (`sheet_view`). Both have RLS per bid.
  - The synthetic fixtures now carry a structural grid, dimensions and view titles, each at its true scale. The enlarged plan is a true 1:50.
- **How to run and demo:**
  1. `make up`, then upload a DXF or PDF plan.
  2. Once parsed, `GET /bids/{bid}/sheets/{sheet}/views` shows each view's kind, scale status and evidence, and its grid.
  3. `POST …/measure` with sheet-mm points returns a length, or a 409 that says why and how to unlock it.
  4. `python -m firebid.evals.pdf_benchmark generate --out DIR`, then `throughput --sheets DIR`, gives the NFR-01 figures below.
- **Requirement IDs covered (test names):**
  - FR-VIS-01:
    - `tests/drawings/test_geometry.py`: lengths, cross-format agreement, methods, the fallback, the model.
    - `tests/db/test_sheet_geometry.py`: stored, indexed by place, cache hit with no parsing, version bump re-extracts.
    - `test_views.py::TestMeasurement::test_a_verified_plan_measures_known_lengths_within_half_a_percent[dxf|pdf]`
  - FR-VIS-05:
    - `tests/drawings/test_views.py::TestMeasurement`: 0.5% on DXF and PDF; unverified, NTS and conflicting refused; calibration unlocks.
    - `tests/db/test_sheet_views.py::TestMeasuring`: the API's 409s.
    - `tests/db/test_sheet_views.py::TestCalibration`: the calibrator recorded and audited, role-gated, and surviving re-detection.
  - FR-VIS-06: `test_geometry.py::TestRasterText`, covering OCR words with confidences, and vector spans with positions and no OCR.
  - FR-VIS-07:
    - `test_views.py::TestWhereThingsAre`: "Grid B2", "Grid B1–C2" and L05, on both formats.
    - `test_sheet_views.py::TestLocating`: the same through the API.
  - FR-VIS-08:
    - `test_views.py::TestViews`: kinds from titles; the enlarged plan is more than 95% inside the general plan in grid space, on both formats.
    - `test_sheet_views.py::TestDetectedViews`
  - NFR-01: `tests/evals/test_geometry_budget.py`.
- **PDF engine benchmark (ADR-002):**
  - Synthetic dense A1 and A0 sheets:

    | Engine | Time per sheet | Peak memory | Line work | Text |
    |---|---|---|---|---|
    | pypdfium2 | 0.40–0.81 s | 152–187 MB | 100% | 100% |
    | pdfplumber | 1.35–2.52 s | 176–211 MB | 100% | 99.0–99.6% |
    | PyMuPDF | 0.25–0.43 s | 111–126 MB | 100% | 100% |

  - Recommendation: no PyMuPDF licence now; re-measure on the real sheets.
- **NFR-01 calculation:**
  - The budget: 300 sheets within 3,600 s on 4 workers, so each sheet may use at most 3,600 × 4 / 300 = **48 worker-seconds** for everything.
  - The run: `pdf_benchmark throughput` put 300 dense sheets (3 × A1, 2 × A0, cycled; no cache, so every sheet is extracted) through geometry extraction and view analysis on 4 processes.
  - Per-sheet geometry and views: **p50 1.68 s, p95 3.60 s, max 4.49 s**.
  - Wall time: **151 s**. That is **4.2% of the hour**, leaving about 44 worker-seconds per sheet for rendering, title blocks and classification.
  - On its own, analysing views took 0.12 s on A1 and 0.22 s on A0.
  - Measured on the development host (16 cores, Windows), not in the sandbox pool; the pool's CPU quota should be checked against these figures when it is sized.
- **Deviations and decisions:**
  - **Coordinates are sheet millimetres with y pointing down**, for every source. A DXF modelspace without a viewport or page setup is placed at the scale its own text states (`1:N`, most common first), else fitted to the page and marked so.
  - **Every primitive is exploded, but its parent is kept.** An insert is recorded and exploded, because symbols match on the insert (P1-04). A dimension is recorded with its measured value and also exploded, which is the exact evidence a scale is verified from.
  - **A PDF dimension is found by pairing.** A numeric figure is paired with a parallel line whose midpoint lies within a quarter of the line's length along it and three text heights across it, so a pipe running past does not count.
  - **Grid spacing checks a scale only when it is dimensioned.** An undimensioned grid does not state its true spacing, so it cannot confirm a scale by itself.
  - **Grid coordinates count by label, not by line**: A is 1, B is 2, and so on. Two sheets that show different parts of one grid then give the same coordinates for the same place, which is how an enlarged plan is found inside its general arrangement whatever its scale.
  - **The grid is found once per sheet** and given to its plan-type views; schematics, sections and details get none.
  - **A calibration survives re-detection** when a view of the same kind is still in the same place. A view marked NTS cannot be calibrated.
  - **Level comes from the view title first** (`LEVEL 5 …` becomes L05), then from the drawing number read in P1-02. The zone comes only from P1-02; drawn zone boundaries are not detected yet.
  - **Views are found by their titles unless the source defines them.** A DXF viewport is taken exactly. Otherwise line work goes to the nearest title, and the title block is left out.
  - **The hot loops are NumPy arrays, not STRtree.** Shapely is used for clipping to viewports; figure pairing and grid search are array operations over all segments and are fast enough (above).
- **Manual checks and results:**
  - Probed the four fixtures in both formats: each gives the same views, level, scale status and grid in DXF and PDF, with extents equal to the millimetre.
  - The benchmark and throughput runs above.
- **Defects found and fixed during the step:**
  - pypdfium2's text-page rectangles merged separate text objects on one line. Text is now read per text object, which took recall from 95–98% to 100%.
  - Benchmark walls that ran off the page counted as missed line work; the rooms now stay on the page.
  - ezdxf applied `dimscale` as a length factor, so figures read 300,000. It was replaced with explicit dimension style overrides.
  - An MTEXT dimension figure was placed by its left edge; attachment points are now honoured.
  - PDF circles, which matplotlib draws as closed curves with no close operator, were not marked closed, so no grid bubble was found in a PDF. A path that ends where it began is now closed.
  - A closed polyline that also repeats its first point gave a zero-length closing segment, which stretched view extents to the page edge. Extents now come from primitive boxes.
  - The sandbox's `/scratch` was owned by root (see P1-02).
- **Known gaps and follow-ups:**
  - **Every figure is from synthetic sheets.** The Done-when items name the real dense sheets, and the benchmark, the NFR-01 run and the scale and grid heuristics must be re-run on the D3 golden set when it arrives. ADR-002's recommendation depends on that run.
  - **No viewer UI yet** for views, calibration or measurement; the API is ready for it.
  - **Drawn zone boundaries** are not detected.
  - **Multi-view sheets** are split by nearest title, which has been tested only on single-view fixtures and title parsing. A fixture with a plan, a key plan and a section on one sheet should follow.
  - The `doc_classification` baseline from P1-02 still awaits a named approver.

## P1-04 · Legend and Symbol Mapping · 2026-09-28

- **Summary:** every symbol a consultant draws maps to one canonical object type, confirmed by a person once and then reused on that consultant's later tenders.
  - Legends are read from the geometry: on dedicated legend sheets and in a plan's corner, but never the title block.
  - Each legend row gets a signature: the DXF block's geometry hash, plus a shape descriptor that ignores position, rotation and uniform scale.
  - A row is resolved in this order: the consultant's confirmed mappings, then keyword rules, then the `symbol_mapper` model. Every answer is a proposal; only a person confirms it.
  - Symbols on the drawings are matched to legend rows or mappings. `counts()` counts only confirmed, countable types on Current sheets. Every other symbol is listed under "unmapped", never dropped.
  - The object library and the mappings are both versioned with history (FR-ADM-02).
  - Built in parts A–H, each committed separately.
- **Key modules / files:**
  - `backend/config/object_library.yaml`: the seed, 16 types, each with its attribute schema and how it is taken off (`count`, `length`, `none`).
  - `backend/config/symbol_rules.yaml`: keyword rules. The file's content hash is the rule version.
  - `backend/src/firebid/drawings/`:
    - `symbols.py`: clusters, descriptor, signatures, matching.
    - `legends.py`: headings and rows.
    - `symbol_rules.py`
    - `crops.py`: a legend row drawn from the geometry as a PNG.
  - `parsing/geometry_dxf.py`: inserts now carry their block's geometry hash and scale (`EXTRACTOR_VERSION` 2).
  - `backend/src/firebid/services/`:
    - `object_library.py`: seed, current or as-of, history, create, change, deprecate, restore.
    - `symbols.py`: consultant keys, legend resolution and reuse, instances, the model step, confirm, correct, reject, counts.
  - `backend/src/firebid/agents/symbol_mapper.py`, route `symbol_map` in `llm.yaml`, prompt `symbol_map/v1`.
  - The `symbol.propose` job, on the ordinary worker.
  - `backend/src/firebid/api/`:
    - `symbols.py`: legend rows, crops, counts, confirm and reject.
    - `library.py`: object types with versions and history; consultants and mappings with history.
  - Migration `0019`: `object_type`, `symbol_mapping`, `legend_entry` and `symbol_instance`, the last two under RLS.
  - Two new permissions: `object_library.change` (senior estimator, system admin) and `symbol_mapping.confirm` (estimator, senior estimator, bid manager).
  - `frontend/src/pages/SymbolsPage.tsx` and `LibraryPage.tsx`; the shared `AuthorisedImage` component.
  - `backend/src/firebid/evals/synthetic_symbols.py`: two consultants, legend sheets and plans.
- **How to run and demo:**
  1. `make up`. Set the project's consultant, then upload a legend sheet and a plan.
  2. On the bid, open **Symbols**. The top shows what is not counted and what is. Each legend row shows its crop, its proposal (keyword rule, model with its confidence, or an earlier tender) and Confirm, Correct and Reject.
  3. Open **Library**: object types with each version's history, plus rename, deprecate and restore for editors; and each consultant's mappings with their history.
- **Requirement IDs covered (test names):**
  - FR-VIS-02:
    - `tests/db/test_symbol_mapping.py::TestOneConfirmationThenReuse::test_a_legend_maps_after_one_pass_and_the_next_tender_needs_none`: one pass on tender 1, then tender 2 from the same consultant (spelt differently) is all `reused`, with no new proposals and no model calls.
    - `TestUnmappedIsNeverCounted`: a plan with no legend counts nothing and lists every instance; a legend read after its plan still maps it; a rejected mapping stays unmapped.
    - `tests/db/test_symbol_api.py::TestTheMappingScreen::test_counts_list_the_unmapped_symbol_and_count_nothing_unconfirmed`: counts requested through the API, with the mystery symbol under `unmapped`.
    - `tests/drawings/test_symbols.py::TestMatchingALegend::test_a_rotated_and_scaled_pdf_instance_matches_its_legend_entry`: a PDF gate valve turned 90° and enlarged 1.6×.
    - Every placement matches in DXF and PDF, and the mystery symbol matches nothing.
    - Hypothesis property: the descriptor ignores rotation, scale and position.
    - `Symbols.test.tsx`.
  - Proposal provenance: `TestProposals`. A model proposal carries model, prompt version (the prompt's content hash) and confidence; a rule proposal carries the rule version; a model answer outside the library is no answer.
  - FR-ADM-02:
    - `tests/db/test_object_library.py`: seed, an edit as a new version, version 1 read back, as-of reads, audit, deprecate and restore.
    - `test_symbol_mapping.py::TestMappingHistory`: confirm, then correct, with version 1 read back and the audit event.
    - `test_symbol_api.py::TestTheLibraryApi`
    - The library tests in `Symbols.test.tsx`.
- **Descriptor separation (synthetic symbols, both consultants, DXF and PDF, four placements each):**
  - The same symbol is always within **0.025** of itself.
  - The closest two different symbols (a gate valve and a non-return valve) are **0.154** apart.
  - The tolerance is **0.07**.
- **Deviations and decisions:**
  - **Rules before the model** (approved). Obvious descriptions are proposed by `symbol_rules.yaml`, recorded with its version; the model gets only the rows the rules cannot decide or disagree on.
  - **Who decides** (approved). Senior estimators and system admins edit the library; estimators, senior estimators and bid managers confirm mappings.
  - **Consultant identity** (approved). The project's consultant name, normalised (case, punctuation, Pte Ltd, Private Limited, (S)). With no consultant named, mappings are keyed to the bid, so nothing is reused across unknown consultants.
  - **A changed symbol** (same signature, different description) is proposed **for that project only**, pre-filled with the consultant's answer. The consultant's confirmed mapping is left alone.
  - **The descriptor is three histograms:** pairwise distances (D2), distance from the centroid, and the angle between line and radius. It comes from even resampling along the whole path. The third histogram was added because D2 alone put a bow-tie gate valve 0.08 from a non-return valve. Whole-path resampling was needed because per-segment sampling over-weighted a PDF's 32-segment circles.
  - **The block hash is of the block definition itself**, in block coordinates. Consultants reuse names, so a name alone is never a match.
  - **The model never escalates on confidence alone** (threshold 0). Every mapping reaches a person on the Symbols page, which shows the confidence. A model answer outside the library is recorded as no answer.
  - **Crops are drawn from the extracted geometry**, not the tender file, so the model job never needs the file. What the model sees is exactly what was extracted.
  - **What an instance is gets looked up when counting**, not stored on it, so a confirmation takes effect at once. Instances are re-matched after every sheet, because files arrive in any order.
  - **A `not_an_object` type** lets a person say a recurring symbol (a grid bubble, a north point) is never taken off, so it stops being raised as unmapped.
- **Manual checks and results:**
  - **Live run through the stack:** a bid created with its consultant, and a legend sheet and a plan uploaded through the API.
    - The real parse job in the sandbox pool found 12 legend rows. The rules proposed five types.
    - With no model credentials in the dev stack, the upright row went to a person, as it should.
    - Confirming through the API counted 4 of each confirmed type; the plan's own legend examples were not counted.
    - The mystery symbol and the unconfirmed upright were listed as unmapped.
    - The crop was served as a PNG, and a senior estimator edited the library.
  - Legend detection and crop rendering ran inside the sandbox container, as the sandbox user.
  - Probed descriptor distances across both consultants' symbols, in all placements and both formats (the separation figures above).
  - Probed legend detection on both formats, on a legend sheet and on a plan's own legend: the same six rows in order, with the title block ("LEGEND AND SYMBOLS") correctly excluded.
- **Defects found and fixed during the step:**
  - **Found only by the live run: organisation-level audit events could not be written by the application role.** The `audit_event` policy's check allowed only bid events, so every object library edit and every mapping decision failed with a 500 in the running product. The tests connect as the table owner, which row-level security does not restrict, so they passed.
    - Mapping decisions are now audited under the bid they were made on.
    - Migration `0020` lets the object library's editors (system admin, senior estimator) write bid-less events, and read and write the organisation chain's links. A new link is hashed onto the previous one, so a writer must be able to read it. Only system admins may read organisation-level events, as before.
    - New tests run the API as the application role (`test_symbol_api.py::TestUnderRowLevelSecurity`), including a database-level refusal for an estimator.
  - **Found only by the live run: an agent that failed with an unexpected error left its run "running" and its work waiting for ever.** Here the Anthropic SDK refused to start without credentials.
    - The agent runtime now escalates any such error to a person.
    - This also fixes the same latent hang in the title-block and document-type checks.
  - **Found only by the live run: nothing could set a bid's consultant.** Reuse per consultant depends on it. It is now a field on bid creation and editing (stored on the project), and on the New Bid form.
  - **Timestamps.** Object library versions took `now()` from the transaction, so versions made in one transaction had one timestamp and an as-of read could not tell them apart. They now take the clock time.
  - **Consultant names.** "(S)" survived normalisation, because `\b` does not match before a parenthesis.
- **Known gaps and follow-ups:**
  - **Every figure is from synthetic legends.** Reuse across real consultants, and the tolerance, must be measured on the D3 golden set (legends from several consultants).
  - **Symbols drawn as loose line work in a DXF** are clustered like a PDF's. Inserts are preferred when present.
  - **Unknown clusters are all recorded**, so a real plan may raise dimension ticks and similar marks as unmapped groups until a person maps them to "not an object". Measure on real sheets whether a recurrence threshold is needed.
  - **Attributes such as the K-factor** are taken only from what the model reads in a legend row. Reading them from tags on the plan belongs to P1-05.
  - **P1-07** is to take counts from `services.symbols.counts`, which already restricts to Current sheets and lists every unmapped symbol.

## P1-05 · Fire Protection Object Detection and Pipe Network · 2026-09-28

- **Summary:** each sheet's confirmed symbols and its pipework become detections, all of them proposals:
  - sprinklers (type, and orientation where the symbol shows one), valves, devices and fittings;
  - risers and drops, both "vertical, not drawn";
  - pipe runs, each with its class (main or branch), a size and a length.

  Pipe is chosen by layer or colour and kept only where it connects to installed symbols. It is noded at symbols, tees and joints (not where lines merely cross) and cut into runs. Sizes are read from annotations and carried along each run; two that disagree are flagged, not guessed.

  Every detection carries its method, evidence, view, grid reference, level and a calibrated confidence. The database refuses one that has neither a location nor a reason for lacking one.

  Built in parts A to H, each committed separately.
- **Key modules / files:**
  - `backend/src/firebid/drawings/`:
    - `pipe_network.py`: candidates, noding, topology, runs; networkx.
    - `pipe_sizes.py`: parsing, attachment, size groups, conflicts.
    - `detection.py`: objects, risers, drops and runs; confidence features; view, grid and level; completeness.
    - `calibration.py`: isotonic regression by pool-adjacent-violators, a versioned map per family.
    - `symbols.py`: angular profiles and `orientation()`; loose clusters now join only primitives drawn alike; `near_match`.
  - `backend/src/firebid/services/detection.py`: detection from stored geometry, views, legend rows, instances and confirmed mappings; the replace-and-write step; vision assist.
  - Jobs:
    - `detection.run`: the whole bid again, queued when a mapping is confirmed or rejected.
    - `detection.vision`
    - Detection is also hooked into `parse_document` after symbols.
  - `backend/src/firebid/api/detections.py`: `GET /bids/{id}/detections`, least confident first; `POST …/detections/run`.
  - `backend/src/firebid/evals/`:
    - `synthetic_network.py`: a wet-pipe installation, with optional mistakes.
    - `detection_calibration.py`: labelled outcomes, and the fit and check.
    - `p1_detection.py`: the suite and predictor.
    - `firebid-eval calibrate`
  - Configuration:
    - `backend/config/detection.yaml`: snap tolerance; vision assist, off by default.
    - `backend/config/calibration/p1_detection.json`: fitted, versioned, never hand-edited.
    - `config/symbol_rules.yaml` gains reducer and riser rules.
  - Migration `0021`: new `detected_object` columns (kind, grid_reference, level, orientation, raw_confidence, features, calibration_version, detector_version, gaps, state) and the `located_or_says_why` check; `pipe_run` under RLS; a versioned `consultant_profile` for pipe layers and colours.
- **How to run and demo:**
  1. `make up`. Create a bid with its design consultant and upload a plan with its legend.
  2. Confirm its mappings on the **Symbols** page. Each confirmation queues `detection.run`.
  3. `GET /bids/{id}/detections` lists every detection, least confident first.
  4. `firebid-eval run --suite p1_detection --report eval/results/p1_detection.md` writes the accuracy report, and `firebid-eval calibrate --suite p1_detection` refits the calibration.
- **Requirement IDs covered (test names):**
  - FR-VIS-03:
    - `tests/drawings/test_detection.py::TestCountsAndLengths` (DXF and PDF): exact sprinkler and valve counts; pipe length per DN within 0.5%; mains, branches and a drop per head; a stray line of the pipe colour isn't pipe; the consultant profile's layer is used.
    - `TestOrientationAndPlace`: sidewall heads face 90°, including in PDF; symmetric PDF symbols claim no orientation; no length on an unverified view; off-network symbols score lower.
    - `tests/db/test_detection_pipeline.py`: nothing is detected until mappings are confirmed; exact counts and lengths from the database; re-detection is queued on confirmation.
    - `tests/evals/test_p1_detection_suite.py`
  - FR-VIS-06:
    - `tests/drawings/test_pipe_sizes.py`: parsing of DN150, 150Ø, Ø65, 150mm, 100 dia, 6", 2-1/2" and 1 1/4" dia; the size carried through valves and tees up to the reducer; disagreeing annotations flagged, both formats.
    - `test_detection_pipeline.py::test_a_conflicting_size_is_stored_unsized_with_its_reason`
  - FR-VIS-09:
    - `test_detection_pipeline.py::TestCompleteness`: every stored detection has method, evidence, view, grid reference and calibrated confidence; the database refuses one that is neither located nor says why; a view without a grid records why.
    - `tests/evals/test_detection_calibration.py`: isotonic properties, including tied scores; the committed calibration within tolerance on unseen seeds.
    - `tests/db/test_vision_assist.py`
    - The detections API lists least confident first.
- **Calibration (synthetic hold-out, `firebid-eval calibrate`: 60 training seeds, 40 hold-out):**

  | Family | Hold-out n | Accuracy | ECE raw | ECE calibrated |
  |---|---|---|---|---|
  | symbol | 1,337 | 84% | 0.087 | **0.015** |
  | drop | 993 | 50% | 0.133 | **0.046** |
  | run | 664 | 81% | 0.098 | **0.028** |
  | overall | 2,994 | | 0.103 | **0.028** (tolerance 0.05) |

  The test repeats the check on seeds 2000 to 2019, which neither the fit nor the hold-out used.
- **Phase 1 accuracy (requirements §14), actuals against targets:**

  | Metric | Target | Synthetic (3 tenders, DXF and PDF) | Golden set |
  |---|---|---|---|
  | Sprinkler count accuracy | ≥ 98% | **100%** | not yet measured |
  | Pipe length error | ≤ 5% | **0.0%** (≤ 0.01% per tender) | not yet measured |
  | Missed items | ≤ 5% | 0% | not yet measured |
  | False detections | ≤ 5% | 0% | not yet measured |

  **Gap analysis:** there is no golden set yet (decision D3), so no metric has a real-world actual. The suite runs on it automatically once files and truth are placed in `eval/files/p1_detection/` and `eval/truth/p1_detection/`. The expected gaps on real sheets, in likely order:
  1. Legend rows that only the model or a person can type (the golden set path uses rules alone, and reports the untyped rows).
  2. Pipes drawn in the same pen as other services, where learning the pipe layer or colour from the sheet picks up the wrong line work (a consultant profile then names it).
  3. Sizes written as leaders, or in a schedule rather than beside the run.
  4. Crossings that are real tees.
  5. Symbols with text inside, which the descriptor ignores.
- **Deviations and decisions** (all approved in the plan):
  - **networkx** added (BSD licence), with types-networkx for mypy. **Isotonic regression is in NumPy**, not scikit-learn.
  - **Consultant profile:** a versioned, organisation-level table. With no layers or colours named, pipe is learnt per sheet: whatever most lines touching installed sprinklers and valves are drawn on or with. The symbols' own strokes are excluded from that vote.
  - **Vision assist is built and off by default.**
    - Only near misses are sent: outside the matching tolerance, within twice it.
    - At most 20 per sheet, answers capped at 0.5 confidence, and it reuses the `symbol_map` route.
    - Vision detections survive re-detection, and a crop is never asked about twice.
  - **Only confirmed mappings are detected.** An unconfirmed symbol stays listed as unmapped (FR-VIS-02).
  - **Lines that cross are not joined.** In a plan they are usually at different heights. A real tee is drawn with a line ending on the other.
  - **Risers come from riser symbols and annotations.** Heights and lengths come from schematics and rules in P1-07. Drops are recorded "vertical, not drawn", with the branch's DN.
  - **Confidence features:**
    - symbols: match distance and method, and whether the symbol is on the network;
    - runs: how the size was found, and a topology-consistency check (a branch sized unlike most of its siblings, or bigger than its main, is suspect);
    - drops: the lower of their sprinkler and run scores.
  - **A size carried along a run scores by the label it came from.** Once raw scores ranked carried sizes below labelled ones although they were more reliable, the calibration map, which only ever rises, could not correct them.
  - **Detections are replaced per sheet on each run** (except vision ones), stamped with detector and calibration versions.
- **Manual checks and results:**
  - **Live run through the rebuilt stack.** A network plan, with its conflicting label, was uploaded through the API.
    - Migration 0021 applied.
    - Five legend rows were reused from the consultant's earlier confirmations; the reducer, riser and upright rows were proposed and confirmed through the API.
    - The worker re-detected on its own. The counts were exact: 16 pendent, 4 upright, 4 sidewall, 1 gate valve, 1 non-return valve, 1 reducer, 1 riser and 24 drops. DN150 was 8,050 mm and DN100 8,250 mm, with 60,000 mm at DN50 plus 12,000 mm flagged as a conflict.
    - The flagged run headed the review list, with its reason.
  - Probed noding, sizing and orientation on both formats while building. Each gave the same network, the same lengths to the millimetre, and the same orientation.
- **Defects found and fixed during the step:**
  - **On a PDF, pipe running into a valve became part of the valve's symbol cluster**, so valves and the riser did not match their legend. Loose clusters now join only primitives drawn alike (same layer and colour).
  - **The pipe colour was once learnt as the symbols' own red.** Strokes that belong to placed symbols are now excluded from the vote.
  - **A split at a symbol used the symbol's box centre**, which sits off the line for a half-round sidewall head, adding 0.01 mm per run. The split now uses the point on the line.
  - **Symmetric PDF symbols reported an orientation**, because lines lying on histogram bin edges made a profile match itself more sharply than its turned copies. Profiles are now smoothed around the circle.
  - **The first isotonic fit did not pool tied scores.** One score split into several blocks at the same point, and interpolation took the last, so calibrated drops said 1.0 and were right 73% of the time. Ties are now one point, scored by their share right.
- **Known gaps and follow-ups:**
  - **No golden set:** there is no real-world accuracy yet (see the gap analysis).
  - **Calibration is fitted on synthetic outcomes only.** It must be refitted with golden-set outcomes (`firebid-eval calibrate`) before its confidences are relied on.
  - **Schematics are not read for riser heights,** and **attributes on plan tags** (such as K-factor labels beside heads) are not associated with detections. Both go to P1-07 and P1-08 as needed.
  - **No workbench UI for detections yet.** That is P1-08; the API is ready.
  - Printing a report containing "≥" fails on a Windows console (cp1252) for every suite. Use `--report` there. The Dev Container and CI are unaffected.

## P1-06 · Specification Attribute Extraction · 2026-09-28

- **Summary:** each fire protection specification becomes a clause tree, and the attributes takeoff needs are read from it, each citing its clause:
  - pipe material, standard and class or schedule, by system and DN range;
  - joining method by DN range;
  - sprinkler type, response, K-factor, temperature and finish;
  - approved makes.

  Rules read the common phrasings; the model is asked only for what the rules can't place or read. Every citation is checked against its clause's words, and one that doesn't hold up is downgraded and flagged. A person confirms, edits or rejects each attribute. Takeoff reads verified values only, and "not specified" otherwise.

  Built in parts A to F, each committed separately.
- **Key modules / files:**
  - `backend/src/firebid/specs/`:
    - `clauses.py`: DOCX by heading styles and numbered paragraphs, PDF by numbering and font; anchors by paragraph or page and line.
    - `sections.py`: heading rules into systems, other trades, or unknown.
    - `attributes.py`: rule extraction with size ranges and place conditions.
    - `citations.py`: the deterministic check.
  - `backend/src/firebid/agents/spec_reader.py`: `spec_attribute_extractor` and `spec_section_finder`.
    - Routes `spec_attribute_extract` and `spec_section_find` in `llm.yaml` (structured output, confidential).
    - Prompts `v1`.
  - Gateway: `TextPart.cache`, the cache hint. The Anthropic adapter sends `cache_control`; OpenAI and Google ignore it.
  - `backend/src/firebid/services/specs.py`: reading, model steps, decisions, and `attributes_for` (the query P1-07 reads).
  - Jobs:
    - `spec.read`: sandbox pool, queued by `register_document` for every specification, whichever way its type was decided.
    - `spec.sections` and `spec.attributes`: ordinary worker.
  - `backend/src/firebid/api/specs.py`: attributes, clause text, decide (`document.review`), and the takeoff view `GET …/spec/for`.
  - `frontend/src/pages/SpecificationPage.tsx`: each attribute's clause opens beside it, with the cited words marked.
  - Migration `0022`: `spec_clause` and `spec_attribute`, both under RLS.
  - `backend/src/firebid/evals/synthetic_spec.py`: the fixture, DOCX and PDF, with 29 known attributes and one contradiction clause for P2-03.
- **How to run and demo:**
  1. `make up`, then upload a fire protection specification (DOCX or PDF) to a bid.
  2. Open **Specification**. Click a clause number to see the words each attribute rests on.
  3. Confirm, edit or reject each attribute.
  4. `GET /bids/{id}/spec/for?system=sprinkler&dn=80` shows what takeoff will use.
- **Requirement IDs covered (test names):**
  - FR-SPEC-01:
    - `tests/specs/test_extraction.py`, both formats: the clause tree, sections, every attribute and nothing else, DN-range joining, the conditional car-park clause, and size ranges as consultants write them.
    - `tests/db/test_spec_attributes.py`: classification queues reading; the clause tree and 29 proposals are stored; reading twice changes nothing; nothing is specified until verified; verified attributes follow the DN ranges; conditional attributes apply only where asked; decisions are versioned and edits checked; the takeoff API.
    - The model call records route, provider, model, prompt version and cost.
    - `Specification.test.tsx` (confirm).
    - The cache hint: `tests/ai_gateway/test_adapter_contract.py::TestTheCacheHint`.
  - FR-SPEC-05:
    - Every rule citation is supported.
    - A wrong clause and a missing clause are flagged.
    - A model answer citing the wrong clause is downgraded to 0.2 and flagged.
    - Citations carry document, revision, clause, anchor and quote.
    - The API resolves every citation to its clause text.
    - `Specification.test.tsx`: the clause panel with the cited words marked, and a failed citation's reason.
- **Deviations and decisions** (approved in the plan):
  - **Rules first, then the model.** The model is asked only about sections the heading rules left unknown, and fire protection clauses the rules read nothing from. On the synthetic specification the rules read all 29 attributes, so no model call is needed there. The model path is tested with the fake adapter.
  - **The cache hint is a flag on text parts.** The specification goes first in every `spec_attribute_extract` call, marked for caching. Only the Anthropic adapter acts on it (OpenAI caches automatically; the Google adapter has no explicit cache yet).
  - **Tables:**
    - `spec_clause`, one row per clause per specification revision.
    - `spec_attribute`, versioned by `lineage_id`. A person's confirmation or edit is a new version, and an edit is re-checked. A person may confirm a value whose citation failed, and the failure stays on record.
  - **Place-limited clauses become conditions.** "In the basement car park" is kept as a condition, and `attributes_for` applies such an attribute only when asked for that place. The car-park clause, which contradicts the drawings, is kept for P2-03.
  - **Only Current specifications feed takeoff.** A superseded specification's verified attributes are not returned.
- **Manual checks and results:**
  - **Live run through the rebuilt stack.** The synthetic specification was uploaded through the API.
    - Migration 0022 applied.
    - The document was classified as a specification by rule and registered Current at revision B.
    - The worker read all 29 attributes (sprinkler 22, hose reel 4, hydrant 3), with every citation checked.
    - A clause opened with its paragraph anchor.
    - The takeoff view said "not specified" until the DN 65 joining rule was confirmed, then "grooved".
- **Defects found and fixed during the step:**
  - **No agent run carried its cost, and model calls had no bid** (from P0-04, affecting every agent). Agents never passed a call context to the gateway, so each model call was metered as a separate record with no bid. Per-bid cost and budget alerts (FR-ADM-05) therefore missed agent calls. The agent runtime now sets the run's context around the agent's work, and the router uses it. The test asserts the call lands on the agent's own run, with its bid, and on no other record.
  - The citation check treated "K80" as one word, so the K-factor 80 was reported unsupported. Letters and digits are now split.
  - "≤ 50" wasn't read as a size range: a word boundary can't come before a symbol.
- **Known gaps and follow-ups:**
  - **Only a synthetic specification has been read.** A real one from the golden set must be checked: numbering styles, tables of pipe schedules, and multi-column PDFs are the likely gaps.
  - **Tables in specifications** (a pipe schedule set out as a table) are not read yet; only clause text is.
  - **Approved makes** are stored, not used, until later phases.
  - **The Google adapter ignores the cache hint.** Explicit context caching there is a follow-up if Gemini becomes the route's model.
  - **PR #20 (commit before response) is separate** and should be merged; it is not part of this step.

## P1-07 · QTO Engine, Measurement Rules and De-duplication · 2026-09-28

- **Summary:** detections on Current sheets become QTO items, each with its Appendix B evidence record:
  - **counted:** sprinklers by type and attributes, valves and drawn fittings by type and size;
  - **measured:** pipe by DN, material, class, joining method and run class, in integer mm, from verified-scale views only;
  - **rule-derived:** drops, risers, and the tees, elbows, reducers and grooved couplings the drawing does not show.

  Each attribute records its source: the drawing, the verified specification (with its citation), or "not specified". Rules are versioned data. Repeats across sheets are grouped, and an unresolved group blocks G1. The net quantity and the allowance are kept apart.

  Built in parts A to E, each committed separately.
- **Key modules / files:**
  - `backend/src/firebid/qto/`, the pure engine: `generate.py` (items, attribute resolution), `rules.py` (drop, riser, coupling and allowance arithmetic), `fittings.py` (fittings from the run topology), `dedup.py` (repeats in grid units), `model.py`.
  - `backend/config/measurement_rules.yaml`: the seed, every default marked "to be confirmed".
  - `backend/src/firebid/services/qto.py`: Current-sheet inputs, rule inputs from sheet notes and bid parameters, recompute, duplicate groups, evidence and completeness, manual items, rule versions, G1.
  - `backend/src/firebid/api/qto.py`: `/bids/{id}/qto/…` (items, history, evidence, duplicates, parameters, manual items, G1, `export.csv`) and `/measurement-rules`.
  - Job `qto.recompute`, queued by detection (the parse job and `detection.run`), by parameter changes and by duplicate decisions.
  - Migration `0023`: `duplicate_group` and `bid_parameter` (both under RLS); `qto_item.item_key`, `inputs_hash` and `derivation`; `measurement_rule.status`.
  - `backend/src/firebid/evals/synthetic_qto.py`: the P1-05 installation drawn whole or in part, giving an enlarged plan, a match-lined pair and a riser schematic. `qto_pipeline.py` runs the engine without a database.
- **How to run and demo:**
  1. `make up`. Upload `FP-L05-201`, `FP-L05-301` and `FP-SCH-001` from `synthetic_qto`, then confirm the legend rows.
  2. `GET /bids/{id}/qto/items`: 13 items. The drops show `drop_length v1`, with the ceiling height taken from the sheet's note.
  3. `GET …/qto/duplicates`: the enlarged-plan group (unresolved) and the schematic's (excluded by default).
  4. `POST …/qto/g1/approve` is refused. Confirm the group with `POST …/qto/duplicates/{id}`, then approve.
  5. `GET …/qto/export.csv`: net, allowance % and allowance quantity in separate columns.
  6. `firebid-eval run --suite p1_detection` reports the duplicate detection rate.
- **Requirement IDs covered (test names):**
  - FR-QTO-01, 02, 05:
    - `tests/qto/test_generation.py` (`TestCounts`, `TestLengths`);
    - `tests/db/test_qto.py::TestTakeoff`: counts and net lengths per DN equal the drawing's, counted once across three sheets; only Current sheets feed takeoff.
  - FR-QTO-03, 04:
    - `test_generation.py::TestRuleDerived`: drops are 24 × (3,300 − 2,750 − 50); a riser is floor-to-floor height × levels served; a level's parameters override the bid's; defaults are labelled.
    - `tests/qto/test_fittings.py`: tees; couplings 12 (DN150) and 7 (DN100), as hand-calculated; elbows; reducers; drawn fittings take precedence.
    - `test_qto.py::TestRuleDerivedInTheApi`.
  - FR-QTO-08:
    - `tests/qto/test_dedup.py`: all three seeded repeats are found; each tender counts the installation once; "not a duplicate" counts both.
    - `test_qto.py::TestDuplicatesAndG1`: evidence locations shown; G1 refused while a group is unresolved, while reading is queued, and on a group only a recompute would find; decisions kept across recomputes.
    - `tests/evals/test_p1_detection_suite.py::test_seeded_duplicates_are_found_and_reported_against_the_target`.
  - FR-QTO-09: `test_qto.py::TestEvidence`: every generated item's record is complete; a deliberately incomplete manual item is flagged and blocks G1.
  - FR-QTO-10: `test_generation.py::TestNetAndAllowance` and `test_qto.py::TestNetAndAllowance` (API and CSV).
  - FR-QTO-11: `test_qto.py::TestManualItems`: count and measured-length items tagged with user and time; measurement refused on the enlarged plan's unverified scale; an edit is a new version; a delete is a rejection.
  - FR-ADM-02: `test_generation.py::TestRuleVersions` and `test_qto.py::TestRuleVersions`: an edit becomes version 2; the old item keeps v1; only a senior estimator can edit.
  - Determinism:
    - `test_generation.py::TestDeterminism`;
    - `test_qto.py::TestRecompute`: the same inputs give the same snapshot hash; verification is kept on unchanged items; a changed input supersedes only the drops, which keep their QTO ID.
- **Deviations and decisions** (approved in the plan, except those marked new):
  - **Seed defaults**, all "to be confirmed" until the business track supplies real values:
    - drop: 3,300 branch elevation, 2,800 ceiling, 50 setting;
    - riser: 4,000 floor-to-floor, 1 level served;
    - couplings: 6,000 random length, one per connected end;
    - allowances: pipe 5%, others 0%.
  - **Heights** come from bid parameters and a reader for "CEILING HEIGHT …" notes. An entered value beats a note, and a level's value beats the bid's.
  - **Schematic and section items** are excluded by default: their group is created `auto_excluded`, and a person may reverse it. Plan groups keep the general plan's member and wait for a person.
  - **New:**
    - **Recompute supersedes a proposal whose inputs changed.** An automated "supersede on recompute" transition was added from Proposed, Edited and Rejected. A new version keeps its QTO ID, so the uniqueness is now per version.
    - **Stricter completeness.** The check also requires location level, calculation note and run metadata. The linked BOQ line (P1-09) and the verifier are not required yet.
    - **New permission `qto.edit`** (estimator, senior estimator).
    - **G1 waits for the takeoff to settle** (found in the live check). G1 is blocked while parse, detection or specification jobs for the bid are pending, and approving recomputes first.
  - **Evaluation:** a run unlabelled on its own sheet is scored at the size carried across the match line, as takeoff uses it.
- **Manual checks and results:**
  - **Live run through the rebuilt stack.**
    - Migration 0023 applied. Three sheets were uploaded and eight legend rows confirmed. The worker detected every sheet and recomputed on its own.
    - Takeoff gave 13 items, the installation counted once:
      - pipe: DN150 8.050 m, DN100 8.250 m, DN50 72.000 m;
      - sprinklers: 16 pendent, 4 upright, 4 sidewall;
      - rule-derived: drops 12.000 m (ceiling 2,750 from the note), riser 4.000 m, 3 + 3 tees.
      - No item was missing evidence.
    - G1 was refused while reading was pending, then refused for the unresolved group, then approved once the group was confirmed.
    - The export showed DN50 as 72.000 net, 5% allowance (3.600), 75.600 in total.
  - **`p1_detection` suite** (synthetic only):
    - sprinkler count 100%; pipe length error 0.0%; missed and false detections 0%;
    - duplicate detection **100% against the ≥95% target** (3 of 3 seeded).
  - **Full backend suite:** 1,303 tests passed. It was run in three parts on the host after a single background run was stopped for low memory.
- **Defects found and fixed during the step:**
  - **G1 could be approved before the takeoff had seen every sheet.** In the first live run the enlarged plan and schematic were still being read, so no duplicate group existed yet. Fixed as described under decisions.
  - **Couplings at a match line were counted from both sheets** (14 DN150 instead of 12). A coupled end is now counted once per place and direction.
  - **The enlarged-plan fixture drew the whole floor's grid and dimensions.** At 1:50 these ran off the sheet, which misplaced the title block, and its region masked the riser and valves. An enlarged plan now draws only the grid lines that bound its area.
- **Known gaps and follow-ups:**
  - **No golden set:** duplicate detection and quantities have been measured on synthetic tenders only.
  - **Verification coverage (FR-REV-04)** joins the G1 checks in P1-08, together with the workbench screens. Until then, generated items can be verified or edited only through the service.
  - **A rule edit does not queue a recompute** for the organisation's bids. It takes effect at the next recompute: any detection, parameter change or duplicate decision, `POST …/qto/recompute`, or approving G1.
  - **Heights are not measured from section drawings,** and specification tables (P1-06's gap) are not read.
  - **Arm-overs and hangers** (FR-QTO-07, P2) are not derived.

## P1-08 · Verification Workbench · 2026-09-28

- **Summary:** the screen where estimators check the takeoff. It has:
  - the drawing, with every proposal over it;
  - a risk-ranked queue;
  - accept, edit and reject, singly or in bulk, with reasons and undo;
  - manual count and length tools;
  - side-by-side duplicate review;
  - panels for naming symbols and calibrating scale;
  - a coverage dashboard, and the G1 action for the Senior Estimator.

  Every edit and rejection is captured as a labelled correction for the evaluation harness.

  Built in parts A to E, each committed separately. The estimator usability session is still to run (see known gaps).
- **Key modules / files:**
  - **Frontend** (`frontend/src/workbench/`, `pages/WorkbenchPage.tsx`):
    - `DrawingViewer.tsx`: OpenSeadragon tiles, a Canvas 2D overlay redrawn per frame from a Flatbush index, and SVG highlights; tools for select, lasso, count and length; the pop-out window over `BroadcastChannel`.
    - `marks.ts`, `render.ts`: the index, hit-testing, lasso and culling.
    - `QueuePanel.tsx`: TanStack Table v8 with TanStack Virtual.
    - `ItemPanel.tsx`, `ManualTools.tsx`, `DuplicatesPanel.tsx`, `GatePanel.tsx`.
    - `review.ts`: the hooks, including optimistic updates and undo.
    - `pages/OverlayBenchPage.tsx`: the performance bench (not linked).
  - **Backend:**
    - `services/review.py`: overlay, queue, coverage, unmapped symbols.
    - `services/review_actions.py`: actions, undo, corrections.
    - `api/review.py`, plus `GET /qto/sheets` and `/qto/overlay` in `api/qto.py`.
    - `POST /symbols/unlisted`.
    - `config/review.yaml`: impact weights (to be confirmed), reason codes, and the coverage policy (100%).
    - Migration `0024`: `review_action` and `correction_event`, both under RLS.
    - `firebid-eval corrections --out`.
  - **Tests:** `e2e/workbench.spec.ts`, `e2e/overlay-bench.spec.ts`, `tests/db/test_review.py`, `src/pages/Workbench.test.tsx`, `src/workbench/marks.test.ts`, `tests/parsing/test_render_alignment.py`.
  - **Usability session guide:** `docs/plan/usability/P1-08-workbench-session.md`.
- **How to run and demo:**
  1. `make up`, then take off a bid as in P1-07.
  2. Open **Workbench** on the bid page.
  3. Click a queue line to see its evidence on the drawing. Shift-drag to lasso. Use A, E, R, J and K; Ctrl+Z undoes.
  4. Add missed items from **Add what was missed**.
  5. Resolve **Duplicates**. Name the grid bubbles under **Symbols & scale**.
  6. Approve under **Coverage & G1** as `senior.estimator@firebid.test`.
  7. Benchmark: `OVERLAY_BENCH=1 npx playwright test e2e/overlay-bench.spec.ts [--headed]`.
- **Requirement IDs covered (test names):**
  - FR-REV-01:
    - `test_review.py::TestOverlay`: marks carry their item, status and band; duplicates are shown as such; evidence boxes.
    - `test_render_alignment.py`: tiles and geometry agree.
    - `marks.test.ts`.
    - E2E: the one-click evidence test, and the main flow.
  - FR-REV-02:
    - `TestQueue`: on a crafted dataset the order is (1 − confidence) × quantity × weight (25, 16, 10, 6.4, 0.2); decided items come last; filters.
    - `Workbench.test.tsx`.
  - FR-REV-03:
    - `TestActions`: bulk accept through the state machine; reasons required; edit is verified as edited; lengths are re-measured; undo; undo is refused once an item has moved on.
    - `TestFalseDetections`: the head and its drop leave, including the enlarged plan's copy; undo restores them.
    - `TestDetectingAgain`.
    - E2E.
  - FR-REV-04:
    - `TestCoverageAndG1`: coverage by items and value; G1 blocked on coverage and on unmapped symbols; naming an unlisted shape.
    - `test_naming_shapes_not_an_object_leaves_the_takeoff_as_it_was`.
    - `Workbench.test.tsx`: G1 disabled with every blocker linked; senior-only approval.
    - E2E: the negative test, and the main flow to G1.
  - FR-REV-06: correction rows carry before, after, reason, detector and calibration version; `test_the_evaluation_harness_exports_the_corrections`.
  - FR-QTO-11 (UI): `TestMarkedCounts`; `Workbench.test.tsx`, where the tools are disabled with the reason on an unverified scale; the E2E manual count.
  - NFR-10: E2E "a QTO line's evidence is one click away".
  - NFR-12: E2E "an action shows its result quickly" (under 200 ms, asserted); the bench below.
- **Deviations and decisions** (approved in the plan, except where marked new):
  - **Edit is verification:** the edit is saved, then verified as edited, in two audited transitions.
  - **Manual items start proposed** and are accepted like any other item.
  - **The QTO item is the unit of review.** Detections are rejected as "not there" from the drawing, which recomputes the takeoff.
  - **Value is weighted by class** until P1-10 brings rates, and is labelled as weighted.
  - **Canvas 2D, not WebGL:** it meets the targets (below).
  - **New:**
    - **G1 also checks unmapped symbols.** A way to name unlisted shapes was needed for this (`POST /symbols/unlisted`).
    - **Marked counts:** a manual count is placed on the drawing, one mark per item, on a verified view, so it carries its location.
    - **A rejected item doesn't block G1** for incomplete evidence.
    - **The G1 check is read-only.**
    - **Dev realm users have fixed IDs,** so a stack rebuild no longer orphans bids.
    - **`@tanstack/react-table` is pinned to v8.** v9 is a new API.
    - **The Senior Estimator runs the whole E2E flow,** as bid membership is outside the step.
- **Performance** (NFR-12). The reference workstation is this Windows 11 laptop: Chromium on Playwright at 1920 × 1080, on an A3 synthetic sheet (tiles 2,480 × 1,754 px) with synthetic marks at plan density.

  | Case | Frame rate (headed, GPU) | Overlay draw per frame (mean / max) | Hit-test (mean / max) |
  |---|---|---|---|
  | No overlay | 60.3 fps | 0.05 / 0.3 ms | n/a |
  | 5,000 marks | 60.2–60.3 fps | 2.6–4.9 / 17.0 ms | 0.002–0.005 / 1.2 ms |
  | 20,000 marks | 60.0–60.1 fps | 7.1–7.5 / 51.1 ms | 0.002 / 0.1 ms |

  - **Headless without a GPU,** the baseline with no overlay is 45–55 fps, so the ceiling there is the viewer, not the overlay.
  - **Action feedback** (accept, key press to the queue saying verified) is **45 ms**, measured in the page and asserted under 200 ms in the E2E. The server confirms in 30–300 ms behind it.
  - **Targets met:** 5,000 marks at 60 fps with feedback under 200 ms; 20,000 marks at 30 fps or more with hit-testing under 16 ms.
  - **Not yet measured:** a true A0 sheet and a real tender's density. The occasional 20,000-mark frame of up to 51 ms is a one-frame spike (the mean stays under 8 ms).
- **Manual checks and results:**
  - **Live runs through the rebuilt stack,** with screenshots: the overlay lines up with the drawing (after the fixes below). The pop-out opened and followed the queue.
  - **The full backend suite passed** (1,330 tests), as did the frontend's 57 unit tests and the four workbench E2E tests.
- **Defects found and fixed during the step:**
  - **No drawing tile ever loaded on the stack** (P1-01). Tile URLs lacked the `/api` prefix, so each tile request got the app's HTML page. P1-01's E2E only checked that the viewer element existed; the workbench E2E now depends on the overlay lining up with the drawing.
  - **DXF tiles and geometry disagreed by about 10%** (P1-01). Matplotlib padded and centred the drawing. Modelspace now fills the image on its extents (renderer version `r2`), with a pixel-level test.
  - **Close-up tiles starved the API** (P1-01). Each tile request rendered its whole level in the sandbox, so a zoom started a dozen renders at once. It is now one render per level, and every tile of it is cached.
  - **A stack rebuild orphaned every bid,** because Keycloak re-imported users with new IDs. The dev realm now pins them.
  - **A person's review was lost when a symbol was named** (from P1-07). Re-detection made new rows, and the inputs hash counted row IDs, so every verified item went back to proposed. The hash now uses what was found and where, and rejected detections stay rejected across re-detection.
  - **A confirmed length-measured type (the riser) was listed as unmapped,** and **a shape no legend explains could not be named at all** (P1-04).
  - **Shapes named "not an object" were cut out of the pipe network.** Some are pipe stubs, and the main lost 0.55 m. Not-an-object types are no longer placed.
  - **Actions and G1 checks held each other up:** every one rewrote all evidence records. Now it's read-only checks, and writes for touched items only.
  - **Late answers cleared a selection, or closed a form, made after them.**
  - **Shortcuts did nothing after searching the queue:** focus stayed in the search box.
- **Known gaps and follow-ups:**
  - **The estimator usability session has not been run.** It needs two or three estimators (`docs/plan/usability/P1-08-workbench-session.md`). P1-08 closes when the findings and changes are added here.
  - **BOQ click-through** (NFR-10 from a BOQ line) starts at the QTO line until P1-09 adds BOQ lines.
  - **Value coverage uses class weights,** marked to be confirmed, until rates arrive (P1-10).
  - **Performance is measured on a synthetic A3 sheet.** A real A0 tender should be measured when the golden set exists.
  - **Recompute holds item rows** for its transaction. An action taken during a recompute can wait a second or two for it (seen in the E2E, which allows 30 s).
  - **The pop-out is checked by hand only.** Its channel is exercised through the page tests, not the E2E.

## P1-09 · BOQ Generation and Client BOQ Reconciliation · 2026-09-28

- **Summary:** the company BOQ is built from the verified takeoff, and the client's bill is read, mapped to it, reconciled and priced back into the client's own workbook.
  - **Our BOQ:** built by a versioned template. Every line keeps the QTO items behind it, and a stable line key, so client mappings survive a rebuild.
  - **The client's bill:** read in the sandbox from its original bytes, which are never written. When the layout is ambiguous, the model proposes the columns and a person confirms them.
  - **Mapping:** the rules propose what they are sure of and the model the rest. Every mapping is a proposal until a person confirms, corrects or rejects it.
  - **Reconciliation:** flags variances over the threshold, plus lines one side has and the other lacks, as clarification candidates.
  - **Exports:** the priced client workbook (only its rate cells, and plain amount cells, are changed), our BOQ, and the reconciliation.
  - **Trace check:** a line with no QTO item that isn't marked as a provisional sum or lump sum blocks G1 and G2.
  - **Minimal G2:** needs G1, a built BOQ and a clean trace. Pricing adds its own checks from P1-10.
  - **Conventions:** measurement conventions per tender, worded for the qualifications.

  Built in parts A to C, each committed separately.
- **Key modules / files:**
  - **Core** (`backend/src/firebid/boq/`):
    - `xlsx_patch.py`: splices `<c>` elements into the sheet XML and re-zips with the same entry order, compression and dates. Formula cells are refused.
    - `reader.py`: header finding, sections, sub-totals, provisional sums, the rate and amount cells; `read_json` is the sandbox's entry point.
    - `generate.py`: templates and roll-up.
    - `matching.py`: kind, size (including "150 x 100") and unit.
    - `reconcile.py`: variance, threshold and conventions.
  - **Service** (`services/boq.py`): building, marked lines, trace, conventions, reading, the column proposal, mapping (rules, then model), decisions, relinking on rebuild, reconciliation, exports, G2.
  - **Agents** (`agents/boq_reader.py`): `BoqColumnReader` and `BoqMapper`, both L1 draft. Routes `boq_columns` and `boq_mapping` are confidential; prompts are v1.
  - **Jobs:**
    - `boq.read` (parse queue), queued when a document is registered as a `boq` xlsx;
    - `boq.columns`;
    - `boq.map`.
  - **API** (`api/boq.py`): `/bids/{id}/boq` (build, lines, markers, `export.xlsx`), `/client` (columns, `priced.xlsx`), `/mappings` (propose, decide), `/reconciliation[.xlsx]`, `/conventions`, `/g2[/approve]`, `/boq-templates`.
  - **Permissions:** `boq.edit` for the estimators, and `boq_template.change` for the Senior Estimator. G1 blockers gain `untraced_lines`.
  - **Data:**
    - migration `0025`: tables `boq_template` and `measurement_convention` (under RLS), and new columns on `boq`, `boq_line` and the client BOQ tables;
    - config: `config/boq_templates.yaml` and `config/boq.yaml` (threshold 5%, conventions; both to be confirmed).
  - **Frontend:** `pages/BoqPage.tsx` (linked from the bid page as **BOQ**).
  - **Evaluation:** `evals/p1_boq.py`, run with `firebid-eval run --suite p1_boq`.
  - **Fixture:** `evals/synthetic_boq.py`, a QS-style client bill with a summary sheet and chart, a title block and logo, merged headings, formula and typed amounts, provisional sums, a hidden list sheet with data validation, comments, defined names and print settings.
- **How to run and demo:**
  1. `make up`. Take off and verify a bid (P1-07, P1-08).
  2. On the bid page, open **BOQ** and choose **Build the BOQ**.
  3. Upload the client's workbook on the documents page. Once it is classified as a BOQ, the worker reads it.
  4. **Propose mappings**, then confirm, correct (pick another line, or "Nothing we measured") or reject each one.
  5. Read the reconciliation, then export the three workbooks.
  6. Set the conventions.
  7. G2 is under `POST /bids/{id}/boq/g2/approve` (no button yet: G2 belongs to the estimate screens from P1-10).
- **Requirement IDs covered** (`scripts/req_coverage.py`: 7 of 7):
  - FR-BOQ-01: `TestCompanyBoq`, `test_generate.py::TestGeneration`.
  - FR-BOQ-02:
    - `test_reader.py`, `test_matching.py`;
    - `TestClientBoq`, `TestMappingAndReconciliation`;
    - `test_eval.py`;
    - `Boq.test.tsx`.
  - FR-BOQ-03:
    - `TestVariance`;
    - `test_120_m_billed_against_128_4_m_measured_is_flagged` (+8.4 m, +7.0%, flagged);
    - reconciliation tests, including client-only and measured-only lines;
    - `Boq.test.tsx`.
  - FR-BOQ-04: `test_xlsx_patch.py` (every other part byte-identical, formulas refused, order and compression kept), `TestExports`.
  - FR-BOQ-05: `TestTrace` (untraced blocks G1 and G2, marking clears it, a reason is required), `Boq.test.tsx`.
  - FR-BOQ-06: `TestConventions`, `test_conventions_are_set_per_tender_...`.
  - FR-ADM-03: template edits make a new version and the old one is kept; `TestApi` covers senior-only editing.
- **Mapping accuracy** (FR-BOQ-02 target ≥ 90%): **100% (14 of 14) by the rules alone, on the synthetic bill.** This figure proves the plumbing, not the accuracy: the fixture and the rules were written together. The model's share and a real bill are unmeasured until the golden set of client bills exists (P1-12).
- **Deviations and decisions** (approved in the plan, except where marked new):
  - **Priced export patches the XML; it does not re-save with openpyxl.** A re-save loses charts, images, validation and comments.
  - **Mappings follow a line key,** not a row ID, so rebuilding the BOQ keeps them.
  - **Minimal G2** from the BOQ side only.
  - **Rates come from `BoqLine.unit_rate`,** which P1-10 fills. Until then the priced copy is the original.
  - **New:**
    - **A line nothing matched is still a proposal** ("nothing matched"), shown to a person while the model is asked, not left without a row. Found live, see below.
    - **A client line with nothing measured behind it is flagged,** unless it is a provisional or lump sum.
- **Manual checks and results:**
  - **Live on the rebuilt stack** (bid BID-2026-048, whose takeoff the workbench E2E verified):
    - the build gave 13 lines, each with its QTO items;
    - the uploaded bill was classified `boq` and read by the worker (14 lines);
    - the rules mapped 11; C3 went to the model, which escalated because there is no key on the dev stack;
    - reconciliation flagged 4: the DN150 main at −6.3%, the flow switch, and the two tees nobody billed;
    - all three exports downloaded. A screenshot of the page was checked.
  - **The priced workbook opened in LibreOffice** (sandbox) and recalculated: rates in, formula amounts and section totals right, typed amounts untouched.
  - **Excel (Microsoft 365, desktop) opened it without repair:** a normal load, with no "[Repaired]" caption. The chart, 3 shapes, 2 comments, 3 names, 14 validated cells and the hidden list sheet all match the original. Excel's recalculated totals match LibreOffice's (bill total 9,557.925).
  - **Tests:**
    - backend: unit suite 934 passed; BOQ database and API tests 23 passed; affected database suites (QTO, review, classification, migrations, RLS, specification) 78 passed;
    - frontend: 60 unit tests passed;
    - ruff, mypy, tsc and oxlint are clean.
- **Defects found and fixed during the step:**
  - **PR #23's action-feedback E2E failed on CI** (213–730 ms against a 200 ms target). A CPU profile at 6× throttle showed the workbench's own work was about 13 ms. The rest was Playwright's trace snapshotting the DOM after the traced key press and wait, and the drawing still loading tiles. The test now presses the key and waits inside one in-page call, once the drawing has settled: 32–50 ms at 6× throttle, 10 ms unthrottled. CI then passed and #23 was merged.
  - **An unmatched client line had no mapping row** when the model could not answer, so the page polled forever and nobody could map it. Fixed as above.
- **Known gaps and follow-ups:**
  - **Model mapping and column proposals are untested against a live model** (fake adapter in tests; no key on the dev stack).
  - **Mapping accuracy on a real bill** waits for the golden set (P1-12).
  - **Rates and a G2 button** arrive with P1-10.
  - **No BOQ E2E yet:** it needs a verified takeoff to start from, which takes minutes to set up. It is covered by the database/API tests, the page tests and the live check.
  - **BOQ line to QTO to drawing click-through (NFR-10)** gives evidence links per line in the reconciliation. The page does not yet open the workbench at an item.
  - **`Specification.test.tsx` once timed out** under a full parallel Vitest run, and passed on rerun and alone. It is pre-existing; watch for it.

## Fix · Reading a real tender set (after P1-09) · 2026-09-28

- **Summary:** the first real tender through the pipeline was a Singapore sprinkler set: 121 A1 sheets in an 83 MB vector PDF. A 6-sheet subset was used: the legend sheet and five plans across two buildings. Nothing on it could be taken off. Four defects were fixed:
  - **Drawing numbers** like `6405(HFC)-F/1B` were cut at the slash. The PDF writes each one as two runs of text, and the pattern expected letters first. The cut numbers also made three sheets "renditions" of the first.
  - **The legend was never found.** It is set out in category columns ("FIRE FIGHTING & ALARM SYSTEM", "VALVES & ACCESSORIES") with no LEGEND heading.
  - **The set was classified as a specification**, and specification reading ran on it.
  - **OCR replaced correct text-layer readings.** It read the paper size (A1) as the revision.
- **Key modules / files:**
  - `drawings/title_block.py`: runs of text a hairline apart are joined; project-numbered drawing numbers are accepted (dates are not); a dash is a first-issue revision; month-year dates are read; a label takes the nearest value that fits its field.
  - `drawings/legends.py`:
    - category headings over a steady run of word-like symbol rows;
    - side-by-side columns kept apart;
    - codes stacked beside symbols rejected;
    - line samples (pipework) and slivers left out of point symbols;
    - symbols signed only for legend rows.
  - `drawings/symbols.py`: `clusters(signed=False)`.
  - `services/classification.py`: a sheet counts as a drawing when its title block gave a drawing number.
  - `services/title_blocks.py`: no OCR fallback when the text layer read the number.
  - Fixtures: `evals/synthetic_symbols.category_legend_sheet`, `GAMMA`.
- **Requirement IDs covered:** FR-DOC-02 (`TestAProjectNumberedTitleBlock`), FR-VIS-02 (`test_category_columns_with_no_legend_heading_are_legends`, `test_a_category_legend_s_symbols_match_their_rows`, `test_a_line_sample_is_no_point_symbol`).
- **Results on the real subset** (local stack):

  | | Before | After |
  |---|---|---|
  | Document type | specification (0.78) | drawing (0.98) |
  | Drawing numbers read | 2 cut short, 4 none | 6 of 6 |
  | Legend rows | 0 | 55 point symbols (69 rows less 14 line types) |
  | Parse time, 6 sheets | 7.8 min | 6.5 min |

  Four sheets whose REV cell (`-`) contradicts their history row (`Rev A`) wait for a person, at 0.60.
- **Manual checks:**
  - three stack reruns;
  - the real file stays outside the repo; tests copy its layouts with made-up numbers and names;
  - 369 drawing/takeoff/evaluation, 56 title-block and 16 symbol tests pass, plus 93 affected database tests before the last two changes and 19 (title-block service, classification) after.
- **Known gaps and follow-ups:**
  - **Head types are confused by matching:** concealed heads match the exposed quick-response symbol. Before confirmation, heads matched against the Rev A notes:

    | Sheet | Matched | Reference |
    |---|---|---|
    | F/3A | 19 | 39 |
    | F/10 | 11 | 10 |
    | F/18 | 179 | 329 |
    | HFT F/1 | 87 | 84 |
    | HFT F/5 | 36 | 50 |

    This needs its own step, with this subset as the test.
  - **Pipework line types in the legend** could identify pipe runs by their line style (not done).
  - **A 121-sheet file is one long job** that shows no sheets until it ends (about 13 s a sheet for title blocks at the time). The progress screen stays blank for a long time.
  - **The first virus scan of the 83 MB file timed out once**, and scanned in 11.5 s when retried.
  - **There is no bid delete in the app.** A test bid was removed directly from the database and object store.

## P1-10 · Rate Library Pricing · 2026-09-29

- **Summary:** estimators price BOQ lines from a company rate library in which every rate has a source (company standard, purchase order or quotation), a reference, an effective date and a validity. Built in parts A (library, matching, provenance, validity, totals, queue weighting) and B (API and screens).
  - **The rate library:** entries are immutable. A changed rate is a new version and the old one is kept, with every line priced from it. An xlsx import is all or nothing, with every problem reported by row and column.
  - **Keys:** each BOQ line gets an item key (type, DN, material, schedule, joining, brand) from what its QTO items agree on.
  - **Matching:** an exact key prices a line by rule. Where entries differ only in something other than type and size, the model proposes one (it is never shown a rate) and an estimator confirms it. Estimators can also choose any entry.
  - **Unpriced:** a line with no entry says "unpriced" and is left out of the total.
  - **Provenance** is enforced in the domain (`domain.pricing.price_from`) and in the database (a check constraint, and a trigger holding rate and amount to the entry's).
  - **Validity:** warnings for an expired rate, and for a rate ending before the tender validity (submission plus `tender_validity_days`).
  - **Totals:** Decimal, half-up per line, section and grand, excluding GST.
  - **Exports:** rates reach the client's workbook (through the P1-09 patcher) and our BOQ export (with each rate's source, and totals).
  - **The review queue** weighs priced items by their rate.
- **Key modules / files:**
  - `pricing/keys.py`, `pricing/rates.py` (matching, validity, totals), `pricing/importer.py` (sandbox entry `read_json`).
  - `domain/pricing.py`; `services/pricing.py`; `agents/rate_match.py`, with route `rate_match` (confidential: no rates sent) and prompt v1.
  - Job `pricing.match`; `api/pricing.py` (`/rates`, `/rates/import`, `/rates/{id}/history`, `/bids/{id}/pricing`, `/run`, `/lines/{id}/confirm`, `/lines/{id}/rate`, `/lines/{id}/candidates`).
  - Permission `rate_library.change` for the Senior Estimator; pricing actions use `boq.edit`.
  - Migration `0026`: rate key parts, `retired_at`, one current version per item/unit/source; BOQ line item key, proposal, method and who priced it; check constraint `priced_from_rate`; trigger `boq_line_price_from_rate`.
  - Config: `config/pricing.yaml` (source preference, to be confirmed); `config/review.yaml` gains `weight_unit_sgd: 40` (to be confirmed).
  - Frontend: `pages/RatesPage.tsx`, `pages/BoqPricing.tsx` (on the BOQ page), a Rates link in the navigation.
  - Fixture: `evals/synthetic_rates.py`.
- **How to run and demo:**
  1. `make up`. As `senior.estimator@firebid.test`, open **Rates** and import a rate list.
  2. On a bid with a built BOQ, open **BOQ**, then **Price from the rate library**.
  3. Confirm proposals, or **Choose…** an entry for a line.
  4. Set the bid's tender validity to get the validity check.
  5. Export our BOQ and the priced client workbook.
- **Requirement IDs covered:** FR-CST-01, in `tests/pricing/test_pricing.py` (keys, matching, validity, hand-worked totals, domain refusal, import report) and `tests/db/test_pricing.py`:
  - `TestProvenance`: domain and database refusal, the trigger, allowances;
  - `TestMatching`: rule, proposal then confirmation, unpriced, a person's choice surviving a rebuild;
  - `TestValidity`; `TestLibrary`: versions, all-or-nothing import; `TestTotals`; `TestExports`: the client-workbook round trip with rates, our BOQ export; `TestQueue`; `TestApi`;
  - frontend: `Rates.test.tsx`.
- **"Done when":**

  | Criterion | Test |
  |---|---|
  | A price without a rate-entry reference is rejected by the domain and the database | `TestProvenance`, `TestDomain` |
  | Rule matches price correctly; proposals wait; unmatched lines show "unpriced" | `TestMatching` |
  | Validity warnings (ends before the tender, expired) | `TestValidity` (unit and database) |
  | Totals match hand calculations, including rounding (0.025 → 0.03 half-up, 0.0125 → 0.01, a 1.005 rate as 1.01) | `TestTotals` |
  | The priced client workbook passes the P1-09 round trip with rates filled | `TestExports`; the P1-09 test now prices from the library |
  | Build log entry | this entry |

- **Deviations and decisions** (approved in the plan):
  - The net quantity is priced; wastage becomes a cost line in P2 (FR-CST-06).
  - Rule matches price at once.
  - Imports are all or nothing.
  - The model never sees a rate.
  - A blank key part matches only a blank; a blank against a value is a partial match.
  - **New:**
    - **The database also holds a rate and amount to the entry's rate** (a trigger), not only the rate reference.
    - **Class weights and rates are on one scale** in the review queue: `weight_unit_sgd`, the SGD value of one weight unit (a sprinkler head).
    - **Provisional and lump sums** keep an amount without a rate: the estimator's allowance, shown as such and totalled separately.
- **Manual checks and results:**
  - **Live on the rebuilt stack** (bid BID-2026-048):
    - the synthetic rate list imported (10 entries);
    - pricing priced 9 of 13 lines by rule and left 4 unpriced (no DN on this bid's riser and gate valve, tees with no exact entry), with the expired DN100 rate warned of; total SGD 3,612.10 excluding GST;
    - after confirming the client mappings, the priced client workbook carries the rates in its own cells;
    - coverage reads "SGD: 8 of 13 items from rates";
    - screenshots of both screens checked.
  - **Tests:**
    - backend unit suite: 974 passed (19 of them pricing);
    - pricing database/API: 18 passed;
    - affected database suites (BOQ, review, takeoff, migrations, RLS): 82 passed;
    - frontend: 15 page tests, including 4 new.
    - ruff, mypy strict (342 files), tsc and oxlint are clean.
- **Known gaps and follow-ups:**
  - **The model's proposals** are tested with a fake adapter only (no key on the dev stack).
  - **Weights and preferences to be confirmed:** `weight_unit_sgd` and `source_preference`.
  - **No rate list from the business yet:** the synthetic one stands in.
  - **Quotations** (FR-CST-02/03), GST (P2-04) and cost build-up (FR-CST-06) are Phase 2.
  - **Order-dependent test:** `test_migrations_leave_application_logging_working` fails when run alone with `test_migrations.py` (on `main` too). It passes in the full suite.

## P1-11 · Phase 1 Hardening, Pilot Support and Exit Evaluation · 2026-10-01

- **Summary:** Phase 1 is measured against its exit criteria and made ready for a gate review. The work came in parts A to G:
  - **A, exit evaluation and shadow mode:** `firebid-eval exit` writes `docs/reports/phase1-exit.md`. It covers:
    - every KPI and exit criterion against its target, sliced by input class and consultant;
    - an indicative real-drawing sample, reported apart from the golden set;
    - live bid measures (`--live`);
    - pending evidence, named as pending;
    - the gap list.

    `firebid-eval shadow` sets an estimator's manual takeoff beside the verified AI-assisted one, line by line, with effort. The workbench records minutes on task (a heartbeat).
  - **B, KPI dashboard:** a KPIs page and AI cost per bid, broken down by route, provider and model.
  - **C and C2, performance:**
    - profiling on real sheets removed two O(n²) hot spots (segment lengths, pairwise symbol matching);
    - the parse job became a staged, per-sheet pipeline ([ADR-010](../adr/ADR-010-staged-per-sheet-parse-pipeline.md)): one job per sheet, each committing its own work, with stalled parse jobs re-queued;
    - real A1 sheets went from 72 s a sheet to 9.4 s.
  - **D, security, privacy and retention:**
    - scans in CI: pip-audit, npm audit, Bandit, Semgrep, gitleaks and Trivy;
    - the ASVS 5.0 Level 2 checklist, a secrets audit and a pen-test scope;
    - nightly retention by configurable policy, and a nightly audit hash chain check.
  - **E, deployment:**
    - Terraform for staging and production on Google Cloud, asia-southeast1 ([ADR-008](../adr/ADR-008-hosting-on-google-cloud.md), proposed): validated, not applied;
    - `firebid-ops deploy-guard`, `restore-drill`, `game-day` and `provider-terms`.
  - **F, runbooks and user material:** deploy, restore, incident response and key rotation runbooks; an estimator quick start; a one-day training outline.
  - **G, load and takeoff benchmarks:**
    - `make load-test` runs 10 bids and 20 users;
    - `make pipeline-benchmark` now also times the first-pass takeoff;
    - the exit report marks evidence that misses its own target, and adds it to the gap list with an action.
- **Key modules / files:**
  - Evaluation: `evals/p1_exit.py`, `evals/shadow.py`, `docs/reports/phase1-exit.md`, `docs/reports/phase1-gaps.yaml` (hand-kept gaps).
  - Benchmarks: `scripts/pipeline_benchmark.py`, `scripts/load_test.py`; results under `eval/results/bench/`, `eval/results/ops/` and `eval/results/shadow/`.
  - Operations: `ops/` (deploy guard, restore drill, game day, provider terms); `infra/terraform/`, `infra/k8s/`.
  - Documents: `docs/security/`, `docs/runbooks/`, `docs/user/`; ADR-008 and ADR-010.
- **How to run and demo:**
  1. `make up`.
  2. `make pipeline-benchmark SHEETS=50`, then `make load-test`.
  3. `firebid-eval shadow --synthetic --bid <shadow_bid>`, with the bid the load test prints.
  4. `make exit-report INDICATIVE=<folder holding the real sample>`. The 6405 sample stays outside the repo.
  5. Open **KPIs** in the app.
- **Requirement IDs covered:** `make req-coverage PHASE=P1` reports **62 of 62 covered**. That is every Phase 1 FR, and NFR-01 to 15. New in this step:
  - NFR-01, NFR-02: `test_parse_pipeline.py`, `test_geometry_budget.py`, `test_p1_exit.py::test_a_load_test_that_misses_its_target_is_reported_with_an_action`;
  - NFR-03, NFR-04: `test_ops.py::TestDeploymentGuard`, the game-day test, and the restore drill tests;
  - NFR-07, NFR-09: `test_retention.py`;
  - NFR-14, NFR-15: `test_effort_and_kpis.py`, `Kpis.test.tsx`.
- **"Done when":**

  | Criterion | Status |
  |---|---|
  | `phase1-exit.md` with every KPI and exit criterion against target, and a gap list | **Done.** The accuracy criteria are *pending*: there is no golden set (D3). |
  | `req-coverage PHASE=P1` covers every Phase 1 FR and the NFRs | **Done:** 62 of 62. |
  | Benchmark and load results against NFR-01 and NFR-02, with misses in the gap list | **Done, all met** (below). |
  | Restore drill in staging; deployment guard blocks near a seeded deadline | **Partly done.** The guard refused a window with 45 bids due within 48 h. The restore drill ran as a **local rehearsal** only: there is no staging (D2). |
  | Security scans in CI with no open high or critical findings; ASVS complete | **Done** (part D). |
  | Shadow comparison report | **Done**, for a synthetic tender: the pilot has not started. |
  | Production in Singapore; provider data terms recorded | **Not done.** The Terraform is written but not applied. The provider data terms are recorded as **unconfirmed** for all three providers (D2). |
  | Provider-outage game day in staging | **Local rehearsal only:** 9 routes fell back, 1 escalated cleanly, 0 failed. |
  | Build log entry with a gate recommendation | This entry. |

- **Deviations and decisions:**
  - ADR-008 (hosting on Google Cloud) is **proposed**, not accepted. Applying it waits on D2.
  - ADR-010 replaces the single parse job.
  - The QTO benchmark confirms the legend by script, as an estimator's first step. The person's minutes on the legend are not counted as machine time.
  - The load test reads NFR-02's "without degradation" as two things: no failed request, and the 2 s workbench p95 kept while ten bids are parsed at once.
- **Manual checks and results** (local Docker Compose stack: one API, one worker, and one sandbox with 2 CPU and 2 jobs at once):

  | Measure | Result | Target |
  |---|---|---|
  | Ingest, 50 synthetic sheets | 3.9 min (4.6 s a sheet); 300 sheets projected at 23.2 min | ≤ 60 min |
  | First-pass takeoff, 50 sheets | 7.0 min of machine time | ≤ 240 min |
  | Load: 10 bids, 20 users | 1,831 requests, 0 failed; workbench p50 0.016 s, p95 0.094 s; all 10 bids taken off in 2.3 min | p95 ≤ 2 s |
  | Restore rehearsal | 120 MB in 0.2 min; 58 tables and 83 audit chains match | RPO ≤ 24 h, RTO ≤ 8 h |
  | Real sheets (6 × A1) | 9.4 s a sheet | — |

  - **Synthetic shadow comparison:** every drawn quantity matched. It differed only on rule-derived DN25 drops and the DN150 riser (now in the gap list).
  - **Tests on 2026-10-01:**
    - backend without `tests/db`: 999 passed, 8 skipped;
    - frontend: 66 passed;
    - ruff, mypy strict (367 files), tsc and oxlint are clean.
  - **Not run on 2026-10-01:** the database suites, because Docker Desktop was failing on the host. They passed with parts A to F.
- **Known gaps and follow-ups:** the gap list in `docs/reports/phase1-exit.md` (kept in `phase1-gaps.yaml`). The ones that decide the gate:
  - **No golden set (D3):** the ≥98% count and ±5% pipe criteria cannot be measured.
  - **Real drawings perform badly.** On the indicative 6405 sample, count accuracy is 1.2% before the legend is confirmed, and head types are confused. This needs its own step.
  - **The shadow pilot has not run:** the −30% QTO effort criterion is unmeasured.
  - **D2 is open:** no staging or production deployment, no provider data terms, and no model run against a real provider.
  - **Smaller gaps:**
    - a job on the default queue whose worker died stays `doing` for ever;
    - there is no bid delete;
    - two order-dependent tests;
    - the earlier notes on restricting owner access and on revisiting partition counts, both for staging.
- **Recommendation for the Phase 1 gate review: do not pass the gate yet. Approve a shadow pilot instead.**
  - **What is ready:** the platform's functional scope is complete, with every Phase 1 requirement covered by tests. It meets NFR-01 and NFR-02 locally with wide margins, and its security, retention, runbooks and deployment code are ready.
  - **Why not pass:** none of the three exit criteria can be measured yet, and the one real drawing set we have shows head-type matching is not ready for live tenders.
  - **Proposed conditions:**
    1. The sponsor decides D2 and accepts ADR-008. Then `terraform apply` for staging, the restore drill and the game day are re-run there, and production is deployed.
    2. D3's golden set is collected (10 to 20 tenders) and `make exit-report` is re-run.
    3. A head-type matching step is run against the 6405 sample and the golden set before the pilot.
    4. The shadow pilot runs on 3 live tenders, with legends confirmed first.
  - **Then:** re-review the gate on that evidence.

## P1-12 · Design Development and Folder Intake · 2026-10-01

- **Summary:** two things a real design-intent tender (MOH, TTSH) needed and the platform could not do. This step was added after the exit report; its requirements (§6.17) are proposed, not approved (ADR-011).
  - **Design development.** A tender drawn as design intent shows mains and leaves the heads and range pipes to the contractor, so the takeoff counted no heads. The platform now reads each plan sheet's design criteria from its notes, waits for a senior estimator or design manager to confirm one, then proposes heads and range pipes by a versioned rule. The proposal is stored as detections and pipe runs marked `designed`, reviewed on the workbench, and taken off as rule-derived items of their own, marked "proposed layout, not drawn".
  - **Folder intake.** A whole folder is sent from the Documents page, in batches, with each file's path kept. Every document has an origin (tender, working, reference), proposed from its path and set by the person sending it. Only a confirmed tender document is read.
- **Key modules / files:**
  - `backend/src/firebid/design/` (pure): `rooms.py` (spaces from the base plan's linework: walls, footprint, rooms, zones, crossed shafts, structural grid removal), `layout.py` (head grid, omissions, head rules, range pipes and feeds), `basis.py` (criteria and design intent from notes; the rule record).
  - `backend/config/design_rules.yaml` (seed of the `sprinkler_layout` rule) and `backend/config/intake.yaml` (origin rules). Every value is "to be confirmed".
  - `backend/src/firebid/services/design.py`, `api/design.py`, jobs `design.basis` and `design.layout`; `db/models/design.py` (`sheet_design`); migration `0030`.
  - `backend/src/firebid/qto/generate.py`: `_designed_heads`, `_designed_pipe`; `qto/model.py`: `DESIGNED`, `Run.origin`.
  - `backend/src/firebid/ingest/origin.py`; `services/ingestion.py` (`source_path`, origin, `set_origin`); `api/documents.py` (`paths`, `origins`, `POST .../origins`, `POST .../origin`); migration `0031`.
  - `frontend/src/pages/DesignPage.tsx`, `FolderUpload.tsx`; `frontend/nginx.conf` (upload body limit).
  - `backend/src/firebid/evals/synthetic_design.py`: a synthetic design-intent sheet.
  - New permission `design_basis.confirm` (senior estimator, design manager).
- **How to run and demo:**
  1. `make up`; the `migrate` service applies migrations `0030` and `0031`.
  2. Open a bid, **Tender documents**, **Or send a whole folder**. Pick the tender folder, check each folder's origin, send.
  3. Confirm the symbols and verify the plan views' scales as usual.
  4. Open **Design development**, **Read the design basis**, select the sheets, pick the criterion, **Confirm and propose a layout** (as `senior.estimator@firebid.test`).
  5. Open the **Workbench**: the proposed heads and pipes are in the review queue. The QTO items end "[proposed layout, not drawn]".
- **Requirement IDs covered:** `make req-coverage PHASE=P1` reports **68 of 68**. New: FR-DSN-01 to 04 (`tests/design/`, `tests/qto/test_designed.py`, `tests/db/test_design.py`, `Design.test.tsx`), FR-DOC-09 and 10 (`tests/ingest/test_origin.py`, `tests/db/test_folder_intake.py`, `FolderUpload.test.tsx`).
- **"Done when":**

  | Criterion | Status |
  |---|---|
  | A synthetic sheet goes from DXF to a proposed layout | **Done** (`tests/design/test_design_sheet.py`). |
  | No layout before a person confirms; named and audited; role refused | **Written, not run:** these are database tests (below). The Design page's side is tested. |
  | What was drawn is counted exactly as before; proposed items carry the rule | **Done** (`tests/qto/test_designed.py`). |
  | A marked-up copy is not read, in a folder or inside an archive | **Written, not run** (database tests). The origin rules and the page are tested. |
  | A folder's files keep their paths; every file accounted for | **Written, not run** (database tests). The page's batching and report are tested. |
  | MOH L10 head count against the estimators' | **Done** (below). |
  | `req-coverage PHASE=P1` covers the new IDs | **Done:** 68 of 68. |
- **Deviations and decisions:**
  - **ADR-011** (proposed): design development is a deterministic estimating aid, never a design; document origin at intake. It adds requirements that the approved specification does not have, so it needs the sponsor.
  - **The step was built without the plan-and-approve pause** its prompt asks for: the product owner asked for both features to be implemented directly. The prompt was written afterwards, to match what was built.
  - **Folder upload goes through the API in batches, not presigned URLs.** The existing presigned route cannot work from a browser: `presigned_url` signs a *download* of the key, and against the object store's internal host name (`seaweedfs:8333`). It is left as it was; fixing it needs a public endpoint, a PUT signature and CORS, and a stack to test on.
  - **RAR archives are refused with a reason**, not opened: a decoder would have to be added to the sandbox image.
  - **Proposed range pipe is a `pipe_run` with `origin = 'designed'`** and class `branch`; the QTO item's classification is `range`.
  - The requirement count asserted in `tests/test_req_coverage.py` went from 104 to 112.
- **Manual checks and results:**
  - **MOH sheet A03-10-01 (10th storey, sheet 1), run through the pure modules from its extracted geometry** (the stack was down):

    | Measure | Platform | SJ M&E's own layout |
    |---|---|---|
    | Criterion read from the notes | 4 m × 3 m, 12 m² a head, K 5.6 (80) | the same |
    | Floor found | 3,220 m² in 153 spaces; 3 omitted (2 stairs, 1 shaft) | – |
    | Heads on the sheet | **517** | **496** drawn (346 in the sheet's own scope) |
    | Range pipe and feeds | 983 m | 480 m in scope, about 690 m pro rata |
    | Time | 8 s to find spaces, 1 s to lay out | – |

    The head count is 4% over. The pipe is about 40% over: each row is fed separately, in a straight line to the nearest drawn pipe, where a designer would run a range across several rows.
  - Earlier in the step the count was 733: areas outside the building were being read as floor, and every room was set out on the open-floor grid. Both are fixed and tested.
  - **Tests on 2026-10-01:** backend without `tests/db`: 1,064 passed, 8 skipped. Frontend: 71 passed. ruff, mypy strict (388 files), tsc and oxlint are clean. Migrations form one chain to `0031`.
  - **Not run:** every database test, including the 23 new ones in `tests/db/test_design.py` and `tests/db/test_folder_intake.py`, the two new migrations against PostgreSQL, and `frontend/nginx.conf`. Docker Desktop's engine returned HTTP 500 all day and would not restart cleanly.
- **Known gaps and follow-ups:**
  - **Done on 2026-10-02** (see the next entry): the database tests ran and passed after one fix to a test.
  - **Match lines (FR-DSN-06).** Each sheet is laid out whole. On L10 sheet 1, about 150 of the 496 heads belong to the next sheet. Until scope is drawn per sheet, the shared area is counted on both unless duplicate detection catches it. `PUT .../design/sheets/{id}/scope` stores a scope polygon; there is no screen to draw one.
  - **One real sheet.** The space finding and the rule defaults were tuned on a single MOH sheet. They need the other 147, and another consultant's drawings, before the figures are trusted.
  - **The design rules are unconfirmed defaults**, taken from SJ M&E's response as it read SS CP 52. A senior estimator must confirm the grid, the omissions and the range-pipe table.
  - **Range pipe is over-estimated** (above). No tees or elbows are derived on proposed pipe.
  - **The workbench does not colour proposed marks differently** from detected ones; the item's description says so.
  - **No export of the layout** (FR-DSN-05).
  - **A document already read as tender input cannot be re-marked** as working or reference; the API refuses and says to supersede its revisions instead.
  - **The company's working drawings are kept but not used.** Comparing a proposal with the team's own marked-up layout would be a good accuracy measure.

## Performance · Backend, measured on a real 121-sheet tender (after P1-12) · 2026-10-02

- **Summary:** the stack was brought back up, the 121-sheet tender (82 MB PDF, 153,043 symbol instances) was run through it twice, and the time was followed to its causes. Detection and takeoff are much faster where nothing changed; two defects that only show at this size were found and fixed.
  - **Detection only does what changed.** Each sheet stores a fingerprint of everything its detection is made from (`sheet_geometry.detection_fingerprint`, migration `0032`). Confirming one symbol detects the sheets that draw it; the others keep their rows, and so their links to verified items. A person can still have every sheet detected (`POST .../detections/run`).
  - **Detection asks once a run, not once a symbol:** legend rows, object types, the pipe profile and the calibration maps are read once (`Lookups`), and each mapping once however many symbols share it.
  - **Repeated triggers are one job.** A mapping decision queues `detection.run` once while one is waiting, and takeoff likewise (`enqueue_once`).
  - **Symbol clustering in arrays.** `drawings.symbols._touching` was a Python loop and 74% of the symbol-shapes stage; it is done in arrays now, and the clustering is found once a sheet and shared by legend reading and symbol reading.
  - **Duplicate finding by neighbours.** `qto.dedup.find` compared every pair of a level's detections and of its runs; it looks each up among its neighbours.
  - **A proposal no longer matches the bid again.** `symbol.propose` ended by matching every instance of the bid again: 153,043 instances, 61 times. A proposal changes one row's mapping, so the instances matched to that row are updated in one statement (`link_instances`). Naming an unlisted symbol matches again only what no legend row claimed.
  - **A page file per sheet.** A PDF is cut once into one PDF a sheet (`services/pages.py`); a sheet's job and a close-up tile's render fetch only that, and fall back to the whole document where there is none.
  - **Heartbeat beside the jobs.** It was a job on the worker's only slot, so any job longer than three minutes turned `/health` red. It is a thread of the worker process now (`jobs/heartbeat.py`).
- **Defects found by the measurement, and fixed:**
  - **Riser items shared keys.** A riser's item was keyed by level, sheet and grid reference. On a sheet with no grid every riser on the sheet had one key, so each recompute created the items anew and superseded almost none: 34,320 riser rows with 44 keys after five recomputes, 55,120 live items in all. Each riser has its own key now, by where it is on the sheet, and by its order there when two are drawn at one point. Keys that were already their own are unchanged. A recompute retires extra live items stored under one key, keeping the one a person decided, else the newest: an affected bid is put right by its next recompute.
  - **`enqueue_once` aborted the caller's transaction when it refused a duplicate.** The savepoint it relied on was not in force, so whatever the caller had done was rolled back at commit, silently. Only `parse.finish` used it and the refusal was never tested. The savepoint is taken on the driver's connection now, and the refusal is tested.
  - **The evidence check asked the database once an item** and rewrote every record at every recompute. It reads the project and the verifiers once, and writes a record only when it changed.
- **Key modules / files:** `services/detection.py` (`Lookups`, `fingerprint`), `services/pages.py`, `jobs/heartbeat.py`, `jobs/enqueue.py`, `drawings/symbols.py` (`_touching`, `candidates`), `qto/dedup.py`, `qto/generate.py` (`_risers`), `services/qto.py` (`recompute`, `completeness`), `services/symbols.py` (`link_instances`, `match_instances`), migration `0032`.
- **Measured** (local stack: one sandbox container, 2 CPU, two sheets at a time; other test runs were going at times, so single figures may be off by tens of per cent):

  | Operation | Before | After |
  |---|---|---|
  | Detect the whole bid | 225 to 274 s | 157.5 s |
  | Detect again, nothing changed | as a full run | 4.2 s |
  | Takeoff recompute, nothing changed | 116.7 s, 56,738 statements | 10.0 s, 21 statements |
  | `symbol.propose`, 61 jobs | 721 s | 5 s |
  | Symbol shapes, 121 sheets | 1,067 s | 683 s |
  | Symbol shapes, the heaviest sheet | 10.8 s | 3.1 s |
  | `/health` during a long job | 503 | 200 |

  - **Parsing 121 sheets took 26 min 56 s**, which projects 300 sheets at about 78 minutes: over NFR-01's 60, on this small pool. A second run took 11 min 35 s, but its tiles and geometry came from the cache, so it is not a like-for-like figure.
  - **Where a sheet's time goes** (first run, summed over 121 sheets): symbol shapes 34%, tiles 24%, geometry 20%, title block reading 11%, views 6%, recording symbols 3%, fetching 1%, waiting for the bid lock 0%.
  - The recompute figure "before" includes the riser defect: 55,120 live items where there should have been 6,888.
  - The tender's 6,885 "risers" come from a legend row the benchmark script confirmed without a person: a stress case, not a typical bid.
- **Tests:** the reference implementations of the clustering and of the all-pairs duplicate search are kept in the tests, and the new code must give the same result on generated cases (`tests/drawings/test_touching.py`, `tests/qto/test_dedup_pairs.py`). A cut page must give the geometry, text and pixels it gave in the document (`tests/drawings/test_pages.py`). Database tests cover the fingerprint, the forced run, one read per mapping, one job for many decisions, the refusal path of `enqueue_once`, the heartbeat, and a recompute that writes nothing when nothing changed.
- **Deviations and decisions:**
  - **The heartbeat now says the worker is alive, not that jobs are moving.** Whether jobs move is read from the queue depths it logs each beat, as the monitoring alert already does.
  - **Recompute is not scoped to changed levels.** Schematic duplicate groups span levels, and an unchanged recompute is 10 s. It waits for a bid that shows the need.
  - **Docker on the host:** Docker Desktop would not start because its data disk (`docker_data.vhdx`) was attached to Windows as a raw disk. Detaching it (elevated) fixed it. The Dev Container was recreated for the repository's present folder; its `postCreateCommand` fails on git's "unsafe repository" check until `safe.directory` is set, which `postStartCommand` does only afterwards.
- **Known gaps and follow-ups:**
  - `parse.finish` is one serial job of 189 to 237 s: it matches every instance again and detects every sheet. With the fingerprint, detection can become a job a sheet.
  - Symbol signatures are what is left of the symbol-shapes stage; title block reading and views have a few very slow sheets (about 30 s). None of the three is profiled.
  - More sandbox processes: sheets do not wait on each other, so throughput should follow the CPUs.
  - Tiles and geometry were not measured on a cold cache after the page-per-sheet change.
  - Two riser detections at one point, 214 times on this tender: probably one riser read twice.
  - `scripts/pipeline_benchmark.py` stops on a dropped connection and writes no result; both runs were totalled from the logs.
  - The existing presigned upload route still cannot work from a browser (see P1-12).

## Performance · Detection as a job a sheet (ADR-010, amended) · 2026-10-02

- **Summary:** `parse.finish` detected every sheet of a document in turn, in one transaction under the bid's lock. Detection is now one `detection.sheet` job a sheet, run side by side in the parser pool; the last one queues `parse.complete`, which queues the takeoff and marks the document `done`.
- **Key modules / files:** `services/parse_pipeline.py` (`finish`, `detect_sheet`, `mark_detection_failed`, `complete`), `jobs/tasks.py` (`detection.sheet`, `parse.complete`), migration `0033` (`sheet.detected_at`, `sheet.detection_error`), `tests/db/jobs.py` (the test job runner follows the new jobs).
- **Decisions recorded the same day:** the product owner accepted ADR-008, ADR-010 (with this amendment) and ADR-011, and approved requirements §6.17. ADR-008 settles the hosting half of D2; the provider data terms (ADR-004) are open. §6.17 is not yet in the `.docx` the requirements file is generated from.
- **Measured** on the 121-sheet tender (local stack, one sandbox container, 2 CPU, two jobs at once; tiles and geometry came from the cache, as in the run it is compared with):

  | Measure | Before | After |
  |---|---|---|
  | `parse.finish` | 189 to 237 s | 82 s |
  | Detecting 121 sheets | inside `parse.finish` | 121 jobs, 83 s of work, about 42 s on two slots |
  | Last sheet read to document `done` | about 4 min 30 s (first run) | 2 min 5 s |
  | Whole document `done` | not timed on a cached run | 15 min 23 s (`ingest-real-121.json`: 922.7 s) |
  | Projected for 300 sheets | — | 38 min, with tiles and geometry cached |

  - The 82 s left in `parse.finish` is matching the bid's 153,043 symbol instances again, and classifying the document.
  - Detection is light in this run (0.7 s a sheet): the benchmark confirms the legend after the document is read, so few symbols are detected at this point. A sheet with confirmed symbols takes longer, and gains more from running side by side.
  - The benchmark script ran to the end this time and wrote its result files.
- **Tests:** `tests/db/test_parse_pipeline.py::TestDetectionASheet` (the document is not done until its last sheet is detected; a sheet whose detection fails is finished with why; a job delivered twice does nothing; the takeoff is queued once).
- **Known gaps and follow-ups:**
  - `detection.run`, after a mapping decision, is still one job for the whole bid (157 s forced). It can queue the same `detection.sheet` jobs.
  - `parse.finish` still matches every instance of the bid again for each document.
  - 300 sheets are now 603 jobs.

## Performance · Symbol matching, and detection after a mapping decision · 2026-10-02

- **Summary:** the two largest costs left at the end of reading a tender.
  - **Symbol matching** (`services.symbols.match_instances`, run by `parse.finish`) read every instance of the bid as a whole row and matched each in turn. It now reads plain columns, works each distinct shape out once, and writes only what changed, an answer at a time.
  - **`detection.run`**, queued by every mapping decision, detected every sheet of the bid in turn in one job. It now finds the sheets the decision reaches, by fingerprint, and queues a `detection.sheet` job for each (`parse_pipeline.detect_again`). The takeoff follows when those sheets' documents complete.
- **A change in behaviour, for unexplained symbols only.** Matching each instance in turn let a later copy of a shape join a group that did not exist when the first copy was read, so one shape could be raised under two keys; and instances were read in whatever order the database gave them, so the groups differed between runs. Now a shape is in one group and instances are read in ID order. On the real tender 274 of 153,043 instances are grouped differently (131 shapes had been split); every match to a legend row or a mapping is the same.
- **Key modules / files:** `services/symbols.py` (`match_instances`), `services/detection.py` (`out_of_date`, `_inputs`), `services/parse_pipeline.py` (`detect_again`; `detect_sheet` looks at a sheet already marked; `complete` leaves a refused document refused), `jobs/tasks.py` (`detection.run`, `detection.sheet` with `force`), `scripts/pipeline_benchmark.py` (waits for a bid's sheet and document jobs too).
- **Measured** on the 121-sheet tender (153,043 instances, 21,824 distinct shapes; local stack, two jobs at once):

  | Measure | Before | After |
  |---|---|---|
  | `match_instances`, nothing to write | 46 s | 9 s |
  | `match_instances`, from nothing matched | 63 s | 21 s |
  | `parse.finish` | 82 s (189 to 237 s before detection left it) | 25 s |
  | `detection.run` | 157 s, one job, every sheet | 3 to 4 s, then one job for each of the 84 sheets the decisions reached |
  | Legend confirmed (12 rows) to takeoff ready | about 40 min of queued whole-bid detections on the first measurement | 2 min 30 s |

  - After the legend was confirmed: 168 `detection.sheet` jobs, 196 s of work, 97 s on two slots; the longest sheet took 61 s. Then the first takeoff, 49 s for 6,888 items.
  - The twelve confirmations made two `detection.run` jobs, and both queued the same 84 sheets: the second job of each pair found its sheet done. Wasted jobs, not wasted detection.
  - Whole document read and detected: 13 min 36 s, tiles and geometry from the cache; 300 sheets projected at 34 minutes on that basis.
- **Tests:** `tests/db/test_symbol_mapping.py::TestMatchingATender` (matching again writes nothing; from nothing it gives the same every time; copies of one unexplained shape are raised as one), `tests/db/test_detection_pipeline.py::TestAfterAMappingDecision` (nothing changed queues no sheet; a changed mapping queues its sheet and the takeoff follows; forcing queues every sheet; a job for a sheet detected meanwhile does nothing).
- **Known gaps and follow-ups:**
  - Two decisions close together queue the same sheets twice.
  - One sheet took 61 s to detect. Not profiled.
  - A job on the `default` queue whose worker stopped stays `doing`: seen again here after each rebuild.

## Performance · Views, pipe network, symbol sampling, design layout · 2026-10-02

- **Summary:** the slowest real sheets of each stage were profiled, and the loops found were replaced. Each replacement gives the result the code it replaced gave, checked against that code kept in the tests as the reference, and on real sheets.
  - **Views** (`drawings/scale._paired_figures`): every line of the sheet was tried against every dimension figure, once a view. Only the lines whose midpoint is within reach of the figure are looked at now, found in a list sorted once.
  - **Pipe network** (`drawings/pipe_network._node`): every piece was tried against every endpoint and every symbol, and every endpoint against every joint so far: 57 million pairs on one sheet. The pairs that cannot meet are ruled out on arrays, a piece at a time; joints are found by the square of the sheet they are in.
  - **Symbol signatures** (`drawings/symbols.Sampler`): the sheet's table was sliced twice for every symbol. The sheet's line work is exploded once and a symbol's is picked out of it. The descriptor works each pair of sample points out once, not twice.
  - **Design layout** (`design.layout`): confirming one sheet's basis laid out every confirmed sheet of the bid again. It is a job a sheet now, and one waiting job a sheet. Finding a floor's rooms read every base line again for every space (`design/rooms._wall_lines`).
  - **Detection after a decision**: `detect_again` queues one waiting job a sheet, so two decisions close together no longer queue the same sheets twice.
- **Key modules / files:** `drawings/scale.py`, `drawings/pipe_network.py` (`_may_meet`), `drawings/symbols.py` (`Sampler`, `_evenly`, `_pairs`), `drawings/geometry.py` (`Segments.closing_from`), `design/rooms.py`, `services/design.py` (`queue_layout`, `lay_out_bid(only=)`), `jobs/tasks.py` (`design.layout` takes `sheet_id`), `services/parse_pipeline.py` (`detect_again`).
- **Measured**, a function at a time on real sheets of the 121-sheet tender:

  | Measure | Before | After |
  |---|---|---|
  | Views, worst sheet (116,710 primitives) | 66 s | 6.7 s |
  | Pipe network, 5,000 real pieces and 400 symbols | 24.9 s | 1.1 s |
  | Symbol shapes, densest sheet (165,795 primitives, 2,312 symbols) | 15.6 s | 4.4 s |
  | Symbol shapes, a sheet of 5,326 small symbols | 9.3 s | about 7 s |
  | Finding rooms, one real floor (153 spaces) | 6.0 s | 3.8 s |

  and the whole tender on the local stack (two jobs at once, tiles and geometry from the cache):

  | Measure | Before | After |
  |---|---|---|
  | Document read and detected | 13 min 36 s | 12 min 0 s (300 sheets projected at 30 min) |
  | Views, all sheets | 155 s | 59 s |
  | Symbol shapes, all sheets | 667 s | 607 s |
  | Slowest `detection.sheet` after the legend is confirmed | 61 s | 16 s |
  | Sheets queued by the second of two decision jobs | 84 of 84 again | 17 of 84 |

  The takeoff is unchanged: 6,888 items.
- **Measured and left alone:**
  - **Title block reading** (317 s): the reading itself is 0.1 s a sheet. The rest is the sandbox starting a fresh interpreter for each call (0.7 s a call in the Dev Container, more on two shared CPUs) and OCR on the three sheets with no readable title block (about 30 s each).
  - **Symbol shapes** is still the largest stage. What is left is the descriptor, about a millisecond a symbol, a symbol at a time; doing a sheet's symbols in one array is the next step and has to give the same signatures to the last bit.
  - **Row-by-row inserts** in detection: the database was 0.7 s of an 89 s detection. Not worth changing.
  - **Region labelling** in `design/rooms.py`: not in the first fourteen functions of the profile. The cost was the wall direction, above.
- **Tests:** `tests/drawings/test_sampling.py` (the sampler against slicing the table; the figures found against every line and every figure), `tests/drawings/test_pipe_nodes.py` (the network against the loop it replaced), `tests/db/test_detection_pipeline.py::TestAfterAMappingDecision::test_two_decisions_close_together_queue_a_sheet_once`, `tests/db/test_design.py` (the job names its sheet; a second change finds it waiting).
- **Known gaps and follow-ups:**
  - Needs a decision: one sandboxed process a sheet rather than one a call (security review); more parser jobs at once (the pool has 2 CPUs and 4 GiB); whether a page with a full text layer and no title block is worth OCR.
  - Not started: pagination on the takeoff and review endpoints, uploads read whole into memory, database pool sizing, the progress stream's polling, database tests in parallel.
  - Tiles and geometry on a cold cache are still unmeasured: every run here had them cached.

## Performance · The symbol descriptor; stalled jobs on every queue · 2026-10-03

- **Summary:**
  - **Symbol descriptor** (`drawings/symbols.describe`): the pair distances are taken a coordinate at a time, and counted into their bins without sorting them first (`_counted`). The descriptors are the same to the last bit.
  - **Stalled jobs**: `system.retry_stalled_parse` looked at the `parse` queue only, so a job on the ordinary worker whose worker stopped stayed `doing` for ever (a legend row that never got its proposal; a `detection.run` left after each rebuild). It is `system.retry_stalled_jobs` now and looks at every queue: every job is idempotent (ADR-006). A job found stalled on its fifth run is failed, not queued again, and logged as `stalled_job_abandoned`; a job queued again is logged as `stalled_job_retried`. Seen working on the local stack: a job planted as `doing` with no worker was queued again by the next sweep and ran.
- **Tried and dropped:** describing a sheet's symbols together, in stacked arrays. It gave the same descriptors and was slower (9.2 s against 6.7 s on a sheet of 5,326 symbols): the cost is the arithmetic, 19,900 pair distances a symbol, not the call a symbol, and a batch of them no longer fits the processor's cache.
- **Key modules / files:** `drawings/symbols.py` (`describe`, `_counted`), `jobs/tasks.py` (`retry_stalled_jobs`, `STALLED_ATTEMPTS`; the old task name is kept as an alias for jobs already queued).
- **Measured** on real sheets of the 121-sheet tender, symbol shapes for the whole sheet:

  | Sheet | Start of 2 Oct | After the sampler | Now |
  |---|---|---|---|
  | 5,326 small symbols (14,346 primitives) | 9.3 s | about 7 s | 4.3 s |
  | 2,312 symbols (165,795 primitives) | 15.6 s | 4.4 s | 3.5 s |

  One symbol's descriptor: 0.85 ms to about 0.5 ms. Not measured again on the whole tender on the stack.
- **Tests:** `tests/drawings/test_sampling.py::TestTheDescriptor` (counted as `np.histogram` counts, on and beside the bin edges; the descriptor against the full square of differences), `tests/db/test_parse_pipeline.py` (a stalled job is queued again on either queue; one that keeps stalling is failed; one whose worker is alive is left).
- **Known gaps and follow-ups:**
  - No alert yet on a job that runs for over an hour with its worker alive.
  - Symbol shapes is still the largest parse stage; what is left is arithmetic that the signature's definition asks for (200 sample points, every pair).
## Performance · What an upload holds in memory · 2026-10-03

- **Summary:** two places where an upload held far more than one file.
  - **An archive** was expanded whole before any of it was stored (`list(expand(...))`): a 200 MB archive may hold 4 GiB. It is read through once to check it, keeping nothing, and then taken a file at a time. An archive that breaks a limit part of the way in is still refused whole, with nothing stored.
  - **A file over the direct-upload limit** was read into memory to find out its size, after the files sent before it had been stored. Sizes are checked first, from the files as received, and the upload is refused before anything is read or stored.
- **Key modules / files:** `services/ingestion.py` (`_ingest_archive`), `api/documents.py` (`_size_of`, `upload`), `api/security.py` (a comment that said uploads were streamed to storage; they are not).
- **Not done: a file is still read whole.** One file at a time is held in memory: up to 200 MB on the direct route, and any size at all on the presigned route (`/uploads/complete` fetches the object to check its digest and scan it). Streaming it means a file-like path through detection, the scanner, the store and archive expansion, and the parsers downstream still take whole files. Recorded as a follow-up, not attempted here.
- **Cost:** an archive is decompressed twice.
- **Tests:** `tests/db/test_document_ingestion.py` (an archive is taken a file at a time; one refused part of the way in stores none of it), `tests/db/test_document_api.py` (a file over the limit is refused before any file is stored).

## P2-01 · Extended Fire Protection Systems and Supports · 2026-10-03

- **Summary:** detection and takeoff now reach past wet-pipe sprinklers.
  - **Object library:** 13 new canonical types, each with its attribute schema: fire pump (duty or standby is an attribute), jockey pump, pump controller, fire water tank, breeching inlet, landing valve, hydrant, hose reel, test header, air compressor (category `equipment`), and dry-pipe, pre-action and deluge valve sets (category `valve`). Keyword rules type their legend rows as consultants name them.
  - **Detection (FR-VIS-04):** equipment is found by legend mapping and symbol matching like every other symbol. Each item of equipment carries the tag written beside it ("FP-01"). Pipe stops at equipment as it does at a valve. Each run records the system its pipework belongs to (hydrant, hose reel, rising main, wet or dry riser, sprinkler), from what stands on it and from how a riser is named.
  - **Schedules:** an equipment schedule drawn on a sheet (a heading with SCHEDULE, a header row with a tag column, a row per tag) gives each tagged item its duty, flow, head and power. A flow is brought to L/min from L/s or m3/h by the column's own unit. Each attribute cites the schedule row, by sheet, in the row's words.
  - **Takeoff (FR-QTO-06):** equipment is counted by type and attributes, with evidence. Equipment drawn only on a schematic (a breeching inlet, often the pumps) is counted from the schematic; equipment of a type a plan shows is not counted again. A rising main is measured by the P1-07 riser rule, with each level's floor-to-floor height taken from the level schedule a schematic or section gives ("L03 FFL +9.000"). Hydrant pipe is measured from the site plan at verified scale, as an item of its own, with the hydrant section's specification attributes.
  - **Hangers and supports (FR-QTO-07):** one hanger per spacing, or part of one, of the pipe measured at a size on a level. The spacing is the verified specification's (attributes `hanger_spacing_mm`, read by rule with its size range and clause) or, where the specification is silent, the company default in the new `hanger_spacing` rule; the item says which and cites it. Seismic braces (lateral and longitudinal, rule `seismic_restraint`) are generated only where a verified `seismic_restraint` attribute requires them.
  - **Evaluation:** a new suite, `p2_systems`, with a synthetic tender of four sheets (pump room, typical floor, site plan, riser schematic), a new metric `equipment_count_accuracy`, a baseline, and `make eval-gate` compares it.
- **Key modules / files:**
  - `backend/src/firebid/drawings/equipment.py` (new, pure): `tags`, `schedules`, `level_marks`, `level_at`, `system_of`.
  - `drawings/detection.py`: tags, systems, a level for what a schematic draws; `DETECTOR_VERSION` is `2`, so every sheet is detected again once. `drawings/pipe_network.py`: equipment is a network node and votes for the pipe layer.
  - `backend/src/firebid/qto/hangers.py` (new, pure): `derive`, `spacing_for`, `default_spacing`, `count`.
  - `qto/generate.py`: equipment items, schedule citations, a run's own system and its specification (`_spec_of`), the rising main's name. `qto/dedup.py`: schematic-only equipment is kept. `qto/rules.py`: `level_parameters`. `qto/model.py`: `Run.system`, `ScheduleRow`; a `SITE` or `EXT` drawing number gives that as the level.
  - `services/qto.py`: `sheet_texts` (one query a recompute), `note_parameters` (ceiling notes and level schedules), `schedule_rows`; `rule_rows` seeds rules an organisation has never had. `services/object_library.py`: `ensure_seeded` adds types an organisation has never had.
  - `specs/attributes.py`: hanger spacing and seismic restraint (`RULES_VERSION` is `spec-rules-2`).
  - `evals/`: `synthetic_systems.py`, `p2_systems.py`, `metrics.equipment_count_accuracy`, new `ObjectType` values; `synthetic_spec.with_supports`.
  - Configuration: `config/object_library.yaml`, `config/symbol_rules.yaml`, `config/measurement_rules.yaml` (`hanger_spacing`, `seismic_restraint`, allowance classes `equipment` and `support`), `config/boq_templates.yaml` (groups Equipment, Hangers and supports).
  - No migration, and no API change: the new data is in existing JSON columns (`detected_object.attributes`, `pipe_run.features`, `qto_item.attributes`).
- **How to run and demo:**
  1. `make up`. Create a bid whose design consultant is `DELTA M&E CONSULTANTS PTE LTD` and upload the four sheets written by `firebid-eval run --suite p2_systems` (under `eval/synthetic/p2_systems/SYS-001/`).
  2. Confirm the legend on the **Symbols** page: the keyword rules propose every row.
  3. `GET /bids/{id}/qto/items`: the duty and standby pumps as separate items with flow, head and power from the schedule; a breeching inlet from the schematic; the rising main at 4.500 m with the level schedule as its source; the hydrant main at 26.250 m; hangers citing the company default.
  4. `make eval-systems` writes `eval/results/p2_systems.md`; `make eval-gate` compares it with its baseline.
- **Requirement IDs covered (test names):**
  - FR-VIS-04:
    - `tests/drawings/test_equipment.py`: tags, schedules (units, stray words between rows, blank cells), level marks, systems, and the legend rules for every new type.
    - `tests/qto/test_systems.py::TestDetection`: every type on every sheet with the exact count; tags with their words as evidence; pipe by size and system; no length from the schematic.
    - `tests/db/test_systems_takeoff.py::TestDetections`, and `TestAnOrganisationFromBeforeThisStep` (an existing library gains the types).
    - `tests/evals/test_p2_systems_suite.py`.
  - FR-QTO-06:
    - `tests/qto/test_systems.py::TestTakeoff`: each type counted once across plans and schematic; attributes cite the schedule row; the breeching inlet kept from the schematic; the rising main from the level schedule; hydrant pipe with the hydrant specification; the same items on a second takeoff.
    - `tests/db/test_systems_takeoff.py::TestTakeoff`: the same through the parse pipeline and the database, with complete evidence records and an idempotent recompute.
  - FR-QTO-07:
    - `tests/qto/test_hangers.py`: hand calculations (72,000 / 3,000 = 24; 8,250 / 4,000 = 3; 8,050 / 4,000 = 3); the clause cited; the company default cited when the specification is silent; an edited rule as version 2; no double count across a match line; seismic braces only for the specification that requires them, citing its clause; a buried hydrant main not hung.
    - `tests/specs/test_extraction.py::TestSupports`: spacing by size range from the clause; a spacing figure not taken for a pipe size; "not required".
    - `tests/db/test_systems_takeoff.py::TestHangers`, and an existing organisation gaining the two rules.
- **Evaluation (`firebid-eval run --suite p2_systems`, synthetic tender, DXF):**

  | Metric | Value |
  |---|---|
  | equipment_count_accuracy | 1.0 (13 types, 4 sheets) |
  | pipe_length_error | 0.0 |
  | missed_item_rate | 0.0 |
  | false_detection_rate | 0.0 |
  | duplicate_detection_rate | 1.0 |

  The legend rows are typed by the keyword rules alone. There is no golden set, so none of this is a real-world figure.
- **Deviations and decisions:**
  - **No plan-and-approve pause.** The step prompt asks for a plan first; the owner's standing preference is to implement directly.
  - **The suite is new (`p2_systems`), not an extension of `p1_detection`,** so each has its own baseline. `p1_detection` reports `equipment_count_accuracy` as well, for a golden set whose truth counts equipment.
  - **The baseline's approver is the build step.** `eval/baselines/p2_systems.json` names "P2-01 build (to be confirmed by the product owner)". A person should accept it under their own name.
  - **Hangers are counted on total length, not run by run.** Runs are cut at every tee and valve, so a count per run would put a hanger on every 200 mm stub. Length over spacing, rounded up, per size and level, is what an estimator calculates by hand.
  - **Hanger and brace items are always taken off** when the rules exist, so every existing takeoff gains hanger items at its next recompute, as proposals. A verified one becomes a line of the company BOQ like any other item. The BOQ convention `hangers` (deemed included by default) is still wording for the qualifications only, as the `fittings` convention is: it does not take lines out of the bill. Three existing tests that list a takeoff or a bill in full were updated for the new items (`tests/boq/test_generate.py`, `tests/db/test_pricing.py`, `tests/db/test_qto.py`).
  - **A hydrant main is not hung** (`exclude_systems: [hydrant]` in the rule): it is taken from a site plan and is buried. The rule is editable where it is not.
  - **Seed defaults, all "to be confirmed":** hanger spacing 3,000 mm to DN50, 4,000 to DN100, 4,500 above; seismic braces at 12,000 mm (lateral) and 24,000 mm (longitudinal) from DN65.
  - **Only a landing valve takes the size of its pipe.** A pump's suction and discharge differ, and neither is its size.
  - **Keys of existing items are unchanged.** A run's system is part of a pipe item's key only when it is not the bid's own system, so items taken off before this step keep their keys and their verification.
  - **Existing organisations gain the new types and rules** the next time their library or rules are read. Nothing they have is changed.
  - **Items on a site plan have the level `SITE`,** and what a schematic draws has the level it names nearest. An evidence record needs a level (FR-QTO-09), and these had none.
- **Manual checks and results:**
  - The synthetic tender through the parse pipeline and database in `tests/db/test_systems_takeoff.py`: 12 passed. Not run on the local stack through the browser.
  - `make check` on the first full run: lint and types clean; backend 1,754 passed and 3 failed, all three being full listings that now include hanger items. They were updated and re-run.
- **Defects found and fixed during the step:**
  - The schedule reader stopped at the first row: the fixture's grid bubbles lie between its rows. Words that do not start at the tag column are now passed over, and the table is bounded by its own columns.
  - Four items had incomplete evidence (no level): the site plan's and the schematic's. Fixed as described under decisions.
- **Known gaps and follow-ups:**
  - **No golden set.** Tenders with pump rooms, hydrants and hose reels are needed (business track) before any figure here means anything on real drawings.
  - **PDF is not measured.** The fixtures are DXF, where a block is matched exactly. On a PDF the new symbols are matched by shape, and how well they are told apart is unknown.
  - **Confidence is not calibrated for equipment.** The calibration map was fitted on sprinkler installations; `calibration_error` is left out of the suite. Refit with golden-set outcomes.
  - **Schedules in a specification are not read** (P1-06 reads no tables). Only schedules drawn on sheets are.
  - **A schedule row with no symbol is not reported.** A pump scheduled but drawn nowhere is silently absent from the takeoff.
  - **The model route does not read the new specification attributes.** `hanger_spacing_mm` and `seismic_restraint` are read by rule only; the `spec_attribute_extract` prompt and schema are unchanged.
  - **Buried and above-ground pipe are not told apart.** Hydrant pipe is one item whatever the sheet.
  - **Proposed (designed) range pipe gets no hangers,** and nor do drops or risers.
  - **Pipe runs are still not identified from legend line styles** (the Phase 1 gap list named P2-01 for it; it was outside this step's prompt).
  - **Pump room pipework takes the rising main's system** when the riser symbol is on it, so its pipe is described as wet rising main.
  - **The workbench and BOQ pages were not changed.** Equipment and support items appear in the existing lists; the BOQ template seed has groups for them, which existing organisations' templates do not gain.

## P2-02 · Revision Deltas, Multi-Bid Projects and Sampling Mode · 2026-10-03

- **Summary:**
  - **Revision comparison (FR-DOC-08):** two revisions of a drawing are aligned (by the grid lines both label alike, then by their frames, then by the offset most like symbols agree on) and compared. Each fire protection element is unchanged, changed (with what it was and is), added or removed, and every change is located on the newer sheet. The workbench has a **Changes** tab that lists them and draws them over the drawing.
  - **Delta QTO (FR-QTO-12):** the takeoff as it stood is recorded when G1 is approved and when an addendum is registered. The delta report is the takeoff now against such a baseline: per item (added, removed, changed with its value before, verification kept) and per BOQ line. When a recompute changes what G1 approved, that approval is reopened for the items that changed, and only those need verifying before G1 is approved again.
  - **Addendum propagation:** `affected_items` now also returns the QTO items, BOQ lines and flagged client BOQ lines (clarification candidates) an addendum changed.
  - **Multi-bid projects (FR-BID-04, ADR-012):** a bid publishes its verified takeoff to its project; the project's other bids adopt it. An edit in an adopting bid changes that bid only. Client BOQs, bills, prices and documents stay bid-scoped.
  - **Sampling mode (FR-REV-05):** a Senior Estimator puts an item category under a sampling policy (tolerable error rate, confidence, errors accepted). A random sample is drawn and recorded with its seed. More errors than accepted sends the category back to full review on that bid; otherwise the Senior Estimator accepts the category on the sample.
- **Key modules / files:**
  - Pure: `drawings/revision_diff.py` (`align`, `diff`), `qto/delta.py` (`report`), `qto/sampling.py` (`plan`, `draw`, `evaluate`).
  - Services: `services/revision_compare.py`, `services/delta.py` (snapshots, report, `reopen_if_changed`, `gate_status`, the three addendum providers), `services/sampling.py`, `services/shared_takeoff.py`.
  - `services/qto.py`: recompute skips adopted items and reopens G1; approving G1 takes a snapshot; an adopted item's evidence link. `services/addenda.py`: registering an addendum takes a snapshot. `services/boq.py`, `services/bids.py`: a reopened approval does not count as passed.
  - API: `api/changes.py` (`/sheets/{id}/revisions`, `/revision-diff`, `/qto/baselines`, `/qto/delta`, `/coverage-policies`, `/review/sampling…`, `/shared-takeoff…`). New permission `coverage_policy.change` (Senior Estimator).
  - Migration `0034`: `takeoff_snapshot`, `review_sample` (bid-scoped, RLS), `coverage_policy` (organisation), `shared_takeoff` (project-level, with the new helper `firebid_can_see_project`), and `approval.reopened_at`, `reopened_reason`, `reopened_items`.
  - Frontend: `workbench/changes.ts`, `workbench/ChangesPanel.tsx`, the Changes tab in `pages/WorkbenchPage.tsx`, three mark colours.
  - Fixture: `evals/synthetic_revision.py` (the general arrangement at R01 and R02, with seeded changes).
  - `docs/adr/ADR-012-one-takeoff-for-a-projects-bids.md`.
- **How to run and demo:**
  1. `make up`. Take off and verify a bid, build its BOQ and approve G1 as in P1-08 and P1-09.
  2. Register an addendum and upload a revised drawing into it. Open **Workbench**, the new sheet, **Changes**: the change list, the changes over the drawing, the delta against "before Addendum N", and "G1 is reopened".
  3. Verify the changed items and approve G1 again.
  4. `POST /coverage-policies/sprinkler` as the Senior Estimator with `{"mode": "sampling", "note": "<the accuracy evidence>"}`; then `POST /bids/{id}/review/sampling/sprinkler/draw`, review the sample, and `…/accept`.
  5. `POST /bids/{id}/shared-takeoff/publish`; on a second bid of the same project, `POST /bids/{id}/shared-takeoff/adopt`.
- **Requirement IDs covered (test names):**
  - FR-DOC-08: `tests/drawings/test_revision_diff.py` (the seeded revision reported exactly; a moved plan aligned by its grid; frames when there is no grid; a fit when neither helps; changes, moves, resized and extended runs); `tests/db/test_revisions_and_delta.py::TestRevisionComparison` (through the pipeline, the database and the API); `workbench/ChangesPanel.test.tsx`.
  - FR-QTO-12: `tests/qto/test_delta.py`; `tests/db/test_revisions_and_delta.py::TestDeltaTakeoff` (unchanged verified items keep Verified at version 1; changed items are Proposed with the superseded version behind them; only the new revision is read; the report matches the seeded changes, per item and per BOQ line; G1 reopened for the changed items only, and approved again once they are verified) and `::TestWhatTheAddendumChanged`; `workbench/ChangesPanel.test.tsx`.
  - FR-BID-04: `tests/db/test_shared_takeoff.py` (the shared baseline visible to both bids; a bid-specific edit leaves the other bid and the published takeoff alone; a later version updates what was not edited and keeps what was; under row-level security each team sees its own client BOQ, bill, prices and documents and none of the other's; the API refuses the other bid; a member of both sees both).
  - FR-REV-05: `tests/qto/test_sampling.py` (the plan against the hypergeometric distribution, with a Hypothesis property; the draw; the verdict); `tests/db/test_sampling_mode.py` (policy versions and audit; Senior Estimator only; a recorded, reproducible sample; a clean sample accepted; an error over the threshold escalates and G1 coverage stays short).
- **Deviations and decisions:**
  - **No plan-and-approve pause,** as the owner prefers.
  - **The sampling plan is computed, not looked up.** The sample is the smallest that catches a lot at the tolerable error rate with the stated confidence, exactly, by the hypergeometric distribution. ISO 2859-1 tables were not reproduced from memory. For 40 items at 5% and 95% the sample is 25; for 500, 54.
  - **A lot is a category's QTO items on one bid,** and an item is a group (16 pendent heads are one item). Lots are small, so small categories are reviewed in full. That is what the arithmetic says; sampling pays on large tenders.
  - **G1 still needs every item verified.** Accepting a category on its sample is a named action by the Senior Estimator that verifies the rest of the lot, each item recording the sample. The BOQ takes verified items only, so leaving the unsampled ones unverified would have dropped them from the bill.
  - **Sampling needs a note:** the accuracy evidence the policy rests on. No category is under sampling by default, and there is no Phase 1 golden-set evidence yet to justify one.
  - **Multi-bid sharing is publish and adopt, not read-through** (ADR-012, status Proposed). No security policy on a bid-scoped table was widened.
  - **Reopening is recorded on the approval** (`reopened_at`, the reason, the items), not as a new decision: `approval.approver_id` must be a person, and no person reopens it.
  - **"Re-process affected sheets only" needed no new code.** A new revision is a new sheet, read once; other sheets' detections are found unchanged by fingerprint; a recompute leaves alone items whose inputs are what they were. The tests assert it.
  - **Elements are matched across revisions by place and attributes.** A symbol that moved is a removal and an addition.
- **Manual checks and results:**
  - Not run on the local stack through the browser. The Changes tab is covered by component tests and the API by database tests.
- **Defects found and fixed during the step:**
  - The clarification-candidate provider looked for the changed item's newest version in the bill, which holds the version the bill was built from. It now matches any version.
  - A formatter the frontend does not use was run by mistake and rewrote two files; they were restored and the edits re-applied.
- **Known gaps and follow-ups:**
  - **A person's "not there" does not carry to the new revision.** A rejected detection is remembered per sheet, and a revision is a new sheet.
  - **Unchanged means the same place on the sheet.** If a consultant re-plots a drawing at another position, every item on it is proposed again. The revision comparison aligns the two; the takeoff's own matching does not use that alignment yet.
  - **Only G1 is reopened.** G2 to G4 arrive in P2-08 and must hook into the same mechanism. A bid's stage is not moved back.
  - **No screens for sampling or the shared takeoff.** Both have APIs; the workbench shows neither yet.
  - **The coverage report does not show the per-category breakdown** in the workbench (`GET /review/sampling` returns it).
  - **A bid with drawings of its own cannot adopt** the project's takeoff.
  - **Clarification candidates are flagged client BOQ lines** until P2-06 builds the clarifications register.
  - **PDF revisions are not measured:** the fixtures are DXF.
  - **ADR-012 is Proposed** and waits for the product owner.

## P2-03 · Full Specification Analysis and Scope Matrix · 2026-10-03

- **Summary:** the specification becomes a cited list of what the contractor must do, is checked against the drawings, and gives a scope matrix.
  - **Obligations (FR-SPEC-02):** thirteen categories (testing, flushing, painting, identification, commissioning, approved makes, warranty, defects liability, maintenance, spares, training, submittals, authority inspections and FSC). Each obligation has its category, the sentence that obliges it, any quantity it states with its unit (a test pressure, a duration, a period, a number of sets or coats), and the clause it cites. The citation is checked against the clause's words. Read by rule when a specification is read; a person confirms or rejects each.
  - **Cross-check (FR-SPEC-03):** deterministic rules, each issue citing both sides.
    - **Conflict:** a note on a Current sheet states a pipe material, class or joining method that the specification states differently for that system and size. A drawing note is read with the specification's own rules, so the two are compared like for like. A clause limited to a place (the basement car park) governs the sheets of that place.
    - **Missing from the drawings:** the specification requires an item (a flow test header) and the takeoff has none.
    - **Missing from the specification:** the takeoff counts a type of item no clause mentions.
    - **Ambiguous:** a clause leaves it open ("or equal", "where required", "as directed"), or two values are given for one thing with nothing to choose between them.

    Open issues are the clarification candidates for P2-06. A person dismisses one with a reason; an issue no longer found is marked resolved.
  - **Scope matrix (FR-SPEC-04):** per system, a row for each obligation category and each interface, with a status (included, excluded, by others, unclear) and the clause it was read from. The estimator changes a status, confirms the matrix, and exports it to xlsx.
  - **UI:** the Specification page has three new tabs (Obligations, Issues, Scope matrix), each row one click from its clause, and an issue from its sheet.
- **Key modules / files:**
  - Pure: `specs/obligations.py`, `specs/crosscheck.py`, `specs/scope_matrix.py`.
  - `services/spec_analysis.py` (`read_obligations`, `find_issues`, `analyse`, `clarification_candidates`, `matrix`, `edit_row`, `confirm_matrix`, `export_matrix`, and `read_obligations_with_model`); `services/specs.py` reads obligations when a specification is read.
  - `api/spec_analysis.py`: `/bids/{id}/spec/analysis/run`, `/obligations`, `/issues`, `/clarification-candidates`, `/scope-matrix` (rows, confirm, `export.xlsx`).
  - Model path: `agents/spec_reader.SpecObligationExtractor`, route `spec_obligation_extract` in `llm.yaml`, prompt `v1`.
  - Migration `0035`: `spec_obligation`, `spec_issue`, `scope_row`, all under RLS.
  - `config/scope_matrix.yaml`: the interface list, marked "to be confirmed".
  - Frontend: `pages/SpecAnalysis.tsx`, wired into `pages/SpecificationPage.tsx`.
  - Fixture: `synthetic_spec.extended()` (sections 6 and 7 and clause 2.4), `EXPECTED_OBLIGATIONS`, `SEEDED_ISSUES`, `EXPECTED_INTERFACES`; `synthetic_qto.car_park_plan`.
- **How to run and demo:**
  1. `make up`. Upload a fire protection specification and the drawings; confirm the legend so the takeoff exists.
  2. Open **Specification**. **Obligations** lists what was read; click a clause number to see the words.
  3. **Check against the drawings**, then **Issues**: each with its clause and its sheet. Dismiss one with a reason.
  4. **Scope matrix**: change a status, **Confirm the matrix**, **Export to Excel**.
  5. `GET /bids/{id}/spec/clarification-candidates` is what P2-06 will read.
- **Requirement IDs covered (test names):**
  - FR-SPEC-02: `tests/specs/test_analysis.py::TestObligations` (every category with its clause and quantities, DOCX and PDF; every citation resolves; quantities as consultants write them); `tests/db/test_spec_analysis.py::TestObligations` (stored on reading, idempotent, a person's decision audited, a model answer checked against its clause); `SpecAnalysis.test.tsx`.
  - FR-SPEC-03: `tests/specs/test_analysis.py::TestCrossCheck` (every seeded issue and nothing else; the P1-06 contradiction with both citations; document and revision on both sides of every issue; the same note on another floor is no conflict); `tests/db/test_spec_analysis.py::TestIssues` (through the parse pipeline; clarification candidates; a dismissal kept and a resolved issue across runs); `SpecAnalysis.test.tsx`.
  - FR-SPEC-04: `tests/specs/test_analysis.py::TestScopeMatrix`; `tests/db/test_spec_analysis.py::TestScopeMatrix` (status and clause on each row; edit, confirm and the exported workbook read back cell by cell; a person's status kept across runs); `SpecAnalysis.test.tsx`.
- **Deviations and decisions:**
  - **No plan-and-approve pause,** as the owner prefers.
  - **Rules first, as P1-06.** On the extended fixture the rules read all thirteen obligations, so no model call is made. The model path is built and tested with the fake adapter.
  - **The model is not queued when a specification is read.** It is asked through `POST …/obligations/read-with-model`. Queued automatically it would add a failed run and an escalation task to every specification while no provider key exists (decision D2).
  - **The model is not used for the cross-check.** Ambiguity is found by its wording. The prompt allows a model "to interpret ambiguous clauses"; nothing here needed it.
  - **An issue with one silent side cites what was looked through:** the specification revision with no such clause, or the Current sheets with no such item. So every issue cites a document and revision on both sides.
  - **A sentence requires an item only when the item is what must be provided.** "Power supply to the fire pump panels shall be provided by the electrical contractor" does not require a fire pump. Found on the fixture.
  - **A sentence that obliges without naming a party is the contractor's** ("shall be painted" is included). One that names nobody and obliges nothing is unclear.
  - **A row the specification does not mention is "unclear"** and links to the specification revision, not to a clause.
  - **A person's status survives re-running the analysis;** the clause the row cites is still updated.
- **Manual checks and results:**
  - Not run through the browser on the local stack. The page is covered by component tests and the API by database tests.
- **Defects found and fixed during the step:**
  - "shall warrant" was not read as a warranty (the pattern wanted "warranty").
  - Regular expressions written through a shell lost their word boundaries to backspace characters, twice. The files were repaired and are now written from script files.
  - Two type errors were hidden by a stale type-checker cache until it was cleared.
- **Known gaps and follow-ups:**
  - **Only a synthetic specification has been analysed.** Real obligations are worded in more ways than these rules read; the model path is there for the rest, unproven against a real provider.
  - **The interface list and the obligation categories wait for the Design Manager's review** (business track). `config/scope_matrix.yaml` is marked "to be confirmed".
  - **Drawing notes are compared for pipe material, class and joining method only.** Sprinkler type is checked through the takeoff's types, not through notes.
  - **Specification tables are still not read** (P1-06's gap), so a pipe schedule set out as a table is not cross-checked.
  - **Issues are recomputed on request,** not when a drawing or the takeoff changes.
  - **A specification revised by an addendum** leaves its superseded revision's obligations out of the lists; a clause-level comparison of the two revisions is not built.
  - **The clarifications register is P2-06;** until then the candidates are a list.

## P2-04 · Supplier Quotations and Cost Build-Up · 2026-10-03

- **Summary:** supplier quotations are captured with the line of the file behind every field, priced in SGD with every step shown, and the bid's cost is built up component by component. The platform never makes a price.
  - **Capture (FR-CST-02):** a PDF, an .xlsx workbook or an .eml email with its attachments is stored, scanned, then read in the sandbox. Rules propose the supplier, quotation number and date, validity, currency, delivery terms, exclusions and each line (description, brand, model, unit, unit price, MOQ, lead time), each with the line of the file it was read from. A person corrects what is wrong, links each line to a BOQ line or a rate-library item key, and a senior estimator confirms.
  - **A confirmed line is a rate-library entry** whose source is the quotation. The bill is priced from it exactly as from any other entry, so P1-10's provenance, validity warnings and database checks apply unchanged (FR-CST-03). A line linked to a BOQ line prices that line on confirmation.
  - **Flags (FR-CST-03):** expired; valid for less than the tender's validity; has exclusions; states no validity.
  - **Landed cost (FR-CST-04):** price × the latest recorded FX rate, plus a buffer, plus freight, insurance and import charges according to the Incoterm. Each step is a stored line with its basis. FX rates are recorded with source and date and never changed. Buffer and import percentages are in `config/pricing.yaml`.
  - **GST (FR-CST-05):** prices are held exclusive. The rate is configuration with an effective date, applied at the bid's pricing date, and shown apart from the total.
  - **Build-up (FR-CST-06):** seventeen components, each a line with its basis and source. Materials, fittings, valves, equipment and wastage come from the priced bill. The other twelve are entered by an estimator as a lump sum or a percentage of a named base, under their name; one nobody has entered shows "not set" and adds nothing.
  - **History (FR-CST-07):** each priced line is compared with past purchase order and project prices for its item key and unit, and flagged beyond a configured tolerance (15%), with low, median, high and the latest price shown.
  - **ERP (FR-CST-08):** `pricing.erp.ErpAdapter` (item master, purchase orders, historical costs). The file adapter loads an export workbook, all or nothing. History is for comparison and prices nothing.
  - **No generated prices (FR-CST-09):** an amount with no rate entry is an estimator's allowance and carries their name. G2 fails on a priced line with neither.
  - **UI:** the BOQ page gains **Supplier quotations** (list with flags, and a check view with each field beside its line of the file) and **Cost build-up** (components, totals, GST, pricing date, prices unlike their history).
- **Key modules / files:**
  - Pure: `pricing/quotation.py` (rules, flags), `pricing/landed.py` (FX, import lines, GST), `pricing/buildup.py`, `pricing/history.py`, `pricing/erp.py`; sandbox reader `parsing/quotation.py`.
  - `services/quotations.py` (`capture`, `correct`, `link_line`, `read_with_model`, `confirm`, `reject`, `flags_of`); `services/costing.py` (`record_fx`, `set_priced_on`, `enter`, `clear`, `build_up`, `comparisons`, `load_erp`, `import_erp_file`); `services/boq.py` (`set_allowance`, `unsourced_lines`, the G2 check).
  - `api/costing.py`: `/bids/{id}/quotations…`, `/bids/{id}/cost/build-up`, `/cost/priced-on`, `/cost/history`, `/cost/allowances/{line}`, `/costing/fx-rates`, `/costing/erp/import`.
  - Model path: `agents/quotation_reader.QuotationReader`, route `quotation_extract` (data class **commercial**), prompt `v1`.
  - Migration `0036`: `quotation`, `quotation_line`, `cost_buildup_line`, `bid_price_basis` (under RLS); `fx_rate`, `erp_item`, `price_history` (organisation-level); `rate.quotation_line_id`, `rate.landed`; `boq_line.allowance_by`.
  - `config/pricing.yaml`: `fx.buffer_percent`, `import_costs`, `gst.rates`, `history.outlier_tolerance_percent`.
  - Frontend: `pages/Costing.tsx`, mounted in `pages/BoqPage.tsx`.
  - Fixture: `evals/synthetic_quotes.py` (three quotations with expected answers, an ERP export).
- **How to run and demo:**
  1. `make up`. Take a bid to a built and priced BOQ.
  2. As a senior estimator: `POST /costing/fx-rates` with `{"currency": "USD", "rate": "1.35", "source": "MAS", "as_of": "…"}`.
  3. On the **BOQ** page, **Add a quotation**. **Check** shows each field beside the line of the file it came from. Choose the BOQ line each quotation line prices.
  4. As a senior estimator, **Confirm the quotation**: the landed cost and its steps appear on each line, and the BOQ line is priced from it.
  5. **Cost build-up**: **Enter…** a figure for labour or margin; set **Priced on**; see GST apart from the total.
  6. `POST /costing/erp/import` with the ERP export, then see **Prices unlike their history**.
- **Requirement IDs covered (test names):**
  - FR-CST-02: `tests/pricing/test_costing.py::TestQuotationCapture`; `tests/db/test_costing.py::TestCapture` (each fixture read with every field and confirmed; a failed scan is not opened; `.msg` refused; a correction audited; a model answer kept only where its line bears it out); `TestApi`; `Costing.test.tsx`.
  - FR-CST-03: `tests/pricing/test_costing.py::TestValidityFlags`; `tests/db/test_costing.py::TestFlags` (expired, shorter than the tender, exclusions; a line priced from a quotation references its line); `Costing.test.tsx`.
  - FR-CST-04: `tests/pricing/test_costing.py::TestLandedCost`; `tests/db/test_costing.py::TestLandedCost` (USD 100.00 FOB at 1.35 lands at SGD 146.65, step by step; no rate, no confirmation).
  - FR-CST-05: `tests/pricing/test_costing.py::TestGst`; `tests/db/test_costing.py::TestGst` (a rate from 2027 leaves a bid priced in 2026 unchanged).
  - FR-CST-06: `tests/pricing/test_costing.py::TestBuildUp`; `tests/db/test_costing.py::TestBuildUp`; `Costing.test.tsx`.
  - FR-CST-07: `tests/pricing/test_costing.py::TestHistory`; `tests/db/test_costing.py::TestHistory`; `Costing.test.tsx`.
  - FR-CST-08: `tests/pricing/test_costing.py::TestErpFileImport`; `tests/db/test_costing.py::TestErp` (loads once, a bad row loads nothing, a non-file adapter).
  - FR-CST-09: `tests/db/test_costing.py::TestNoGeneratedPrices` (G2 fails, then passes once the line is unpriced or the allowance named; the name survives a rebuild; the database refuses an entered cost with no name); `Costing.test.tsx`.
- **Deviations and decisions:**
  - **No plan-and-approve pause,** as the owner prefers.
  - **Rules first, the model for the rest.** The prompt says "extraction via structured outputs". The rules read all three fixtures completely, so no model call is made for them. The model is asked by a person (`…/read-with-model`) for fields the rules missed. An answer is kept only if the line it cites contains the value; a unit price must be a figure written on that line.
  - **`.msg` is not read.** No parser for Outlook's format is installed. The refusal says to save the email as `.eml`.
  - **The landed cost is the rate.** A quotation line's rate-library entry holds the SGD unit cost after FX, buffer and import lines, with the breakdown beside it, so the bill's rate is the cost the company bears.
  - **The FX rate used is the latest recorded on or before the day of confirmation.** A foreign quotation cannot be confirmed with none recorded.
  - **Confirming is the senior estimator's** (`supplier_price.select`); uploading, correcting and linking are the estimator's.
  - **An allowance's name is checked at G2, not by a database constraint,** because allowances entered before this step have none. They hold up G2 until an estimator re-enters them.
  - **A failed scan leaves no quotation record.** The bytes are stored (store, scan, then parse) and the refusal is logged.
  - **Labour is an entered lump sum** until P2-05, as the prompt says.
- **Manual checks and results:**
  - Not run through the browser on the local stack. The views are covered by component tests and the API by database tests.
- **Defects found and fixed during the step:**
  - "firm" was read as the currency symbol RM, and "US$" as "S$". A code is now read first, and a symbol only before a figure.
  - An email's own `Date:` header was taken as the quotation's date.
  - A list of exclusions ran on into the lines after a blank line.
- **Known gaps and follow-ups:**
  - **Only synthetic quotations have been read.** Real ones need sample files from the business track; scanned PDFs are not read at all (no OCR here).
  - **The cost build-up template, the FX policy, the import percentages and the outlier tolerance are placeholders** to be confirmed by the business.
  - **The ERP API adapter waits for decision D4.** The file format is this step's own.
  - **GST rates are read from configuration at start-up,** so a change needs a restart.
  - **Import percentages cannot yet be overridden per quotation through the API** (the calculation supports it).
  - **The build-up's direct lines group the bill by its headings;** a bill with other headings puts everything under materials.
  - **Quantity breaks, discounts and a quotation's own totals are not read.** MOQ is kept as text and not checked against the bill's quantity.

## P2-05 · Labour Estimation · 2026-10-03

- **Summary:** labour is estimated line by line from a sourced productivity library, visible multipliers and an hourly rate built up from rate tables. Every factor can be traced.
  - **Productivity library (FR-LAB-01):** man-hours per unit for an item type, optionally by DN and joining method (a metre of pipe, a sprinkler by type, a valve assembly, an equipment item), with its trade. Each entry's source is the company standard, a historical project (which one) or an estimator's judgement (whose). An entry without one is refused by the importer, the service and the database. Imported from xlsx, all or nothing; versioned like the rate library.
  - **Matching:** a BOQ line takes the most specific entry for its item key and unit. An entry with no DN is for every size; an entry for `sprinkler` is for every sprinkler type without its own. A line with no entry has no hours and says so.
  - **Multipliers (FR-LAB-02):** a catalogue in `config/labour.yaml`: four installation height bands, difficult access, MEP congestion, basement, occupied or live building, night work, high-rise logistics. Each has a value, source and rationale. The platform proposes a height band from a level's ceiling height, basement from a level's name and high-rise from the levels served. Nothing is applied until an estimator confirms it, for the bid or for a level. A labour line shows baseline hours and each multiplier separately, with who confirmed it.
  - **Rate build-up (FR-LAB-03):** per grade, an hour's wages, foreign worker levy, accommodation, transport, insurance (WICA, a percentage of wages), overtime premium and share of a supervisor. A trade's rate is its crew's grades by their share of hours. Tables are effective-dated configuration; a bid takes the table in force on its pricing date.
  - **Outputs:** hours and cost per BOQ line, per system (the bill's sections) and by trade.
  - **Cost build-up:** the labour line is now calculated from the estimate, with its basis. An estimator's own figure still stands in its place and says what the estimate came to.
  - **UI:** the BOQ page gains **Labour**: site conditions to confirm, the line table with every factor, totals by system and trade, and each trade's rate build-up with the table's effective date.
- **Key modules / files:**
  - Pure: `labour/productivity.py`, `labour/importer.py`, `labour/multipliers.py`, `labour/rates.py`, `labour/estimate.py`; `pricing/buildup.py` (`Calculated`).
  - `services/labour.py` (`import_productivity`, `set_entry`, `propose`, `decide`, `estimate`, `labour_basis`); `services/costing.build_up` passes the labour line.
  - `api/labour.py`: `/labour/productivity` (list, add, import, history), `/labour/catalogue`, `/bids/{id}/labour`, `/bids/{id}/labour/conditions` (and `/propose`).
  - Migration `0037`: `labour_productivity` (organisation-level), `labour_condition` (under RLS).
  - `config/labour.yaml`: multipliers, proposal rules, rate tables. All marked "to be confirmed".
  - Frontend: `pages/Labour.tsx`, mounted in `pages/BoqPage.tsx`.
  - Fixture: `evals/synthetic_labour.py`.
- **How to run and demo:**
  1. `make up`. Take a bid to a built BOQ.
  2. As a senior estimator: `POST /labour/productivity/import` with a productivity list (columns: type, DN, joining, description, unit, man-hours per unit, trade, source type, source reference).
  3. Set a ceiling height for a level (takeoff parameters), then on the **BOQ** page, **Labour**: **Propose from the bid's parameters**, and **Confirm** the height band. Add night work for the whole bid with a reason.
  4. The line table shows baseline hours, each multiplier, hours, rate and cost. Open a trade under **Labour rates** for its build-up.
  5. **Cost build-up** shows Labour as calculated, with the hours and the rate table's date.
- **Requirement IDs covered (test names):**
  - FR-LAB-01: `tests/labour/test_labour.py::TestProductivityLibrary` (an unsourced entry refused; the most specific entry used; the list read; problems by row and column), `::TestHours`; `tests/db/test_labour.py::TestLibrary` (imported with sources; a changed figure is a new version; an unsourced list imports nothing; the database refuses a blank source; a judgement under the estimator's name), `::TestEstimate`, `::TestApi`; `Labour.test.tsx`.
  - FR-LAB-02: `tests/labour/test_labour.py::TestMultipliers` (catalogue complete; one without source or rationale refused; bands; level and bid scope; proposals), `::TestHours` (72 m × 0.30 h × 1.1 × 1.1 × 1.2 = 31.36 h); `tests/db/test_labour.py::TestEstimate` (a proposal changes nothing; confirmed multipliers shown one by one; a named person and a reason), `::TestApi`; `Labour.test.tsx`.
  - FR-LAB-03: `tests/labour/test_labour.py::TestRateBuildUp` (the hand calculation of each component; a changed levy applies from its date), `::TestInTheCostBuildUp`; `tests/db/test_labour.py::TestRates` (a bid priced the day before the change keeps the old rate; the build-up's labour line), `::TestApi`; `Labour.test.tsx`.
- **Deviations and decisions:**
  - **No plan-and-approve pause,** as the owner prefers.
  - **The estimate is not stored.** It is worked out from the current bill, the library, the confirmed conditions and the rate table each time. Nothing can go stale; a G2 snapshot of it is not kept yet.
  - **Multipliers and rate tables are configuration, not database tables.** The prompt calls the multipliers "a configurable catalogue" and the statutory values "configuration". Which multipliers apply to a bid is data, confirmed by a person.
  - **Risk flags do not exist yet (P2-07),** so proposals come from bid parameters only. Access, congestion, occupied building and night work are added by an estimator with a reason.
  - **One height band a line:** a level's own band stands in for the bid's.
  - **Overtime is a premium on a share of hours** (10% of hours at 1.5 times), and supervision one supervisor to eight workers. Both are table values.
  - **The labour line in the build-up is calculated; an entered figure overrides it** and shows the calculated amount beside the estimator's name.
  - **Changing the library is the senior estimator's** (`labour_productivity.adjust`); confirming conditions is the estimator's.
- **Manual checks and results:**
  - Not run through the browser on the local stack. The view is covered by component tests and the API by database tests.
- **Defects found and fixed during the step:**
  - A proposal's basis read "ceiling height 5200.000 mm"; figures are now written as a person writes them.
  - A response model named like an existing one (`ImportOut`) changed that one's generated name and broke the Rates page's types; it is now `ProductivityImportOut`.
- **Known gaps and follow-ups:**
  - **Every figure is a placeholder:** the multipliers, the rate tables and the synthetic productivity list. The company's standards and tables are business-track inputs.
  - **Pipework, fittings, valves and equipment are billed for the building, not by level,** so a level's multiplier reaches only the lines billed by level (sprinkler heads in the standard template). Building-wide lines take the bid's multipliers only.
  - **Ceiling heights read from sheet notes are not used for proposals,** only those an estimator entered as bid parameters.
  - **Rate tables are read at start-up,** so a change needs a restart, as GST does.
  - **Crew-days and the manpower histogram are FR-LAB-04 (P3); learning from actual hours is FR-LAB-05 (P4).**
  - **No page for the productivity library itself:** it is listed and changed through the API.

## P2-06 · Tender Clarifications Register · 2026-10-04

- **Summary:** flagged issues become evidence-backed tender clarifications, with a register, approvals, responses and qualifications. The platform sends nothing to a client.
  - **Tender clarifications only (FR-RFI-01):** the `kind` column reserves `construction_rfi`; the service refuses to create one and the UI offers no choice.
  - **Candidates:** open specification issues (conflicts, missing items, ambiguous clauses), flagged bill variances (quantity differences, bill items with nothing measured, items measured but not billed) and scope rows left unclear. Each carries its evidence. A candidate already in a clarification is not offered again.
  - **Drafting (FR-RFI-02):** composed by rule from one candidate or a confirmed group: number (`TC-001`), subject, project, level/grid, sheet and revision, problem, evidence, options, potential cost and programme impact, required reviewer. A draft with no evidence is refused by the draft's own model and not saved; the database refuses one too. Evidence can be added to, never emptied.
  - **Options and approvals (FR-RFI-06):** every option is worded "Recommendation: …". A clarification has engineering content when its issue is about an engineering subject (pipe material, class, joining, sprinkler type, a required item) or its words name one (`config/clarifications.yaml`). The Bid Manager approves for issue; engineering content needs the Design Manager first, who may flag "QP input needed".
  - **Register (FR-RFI-04):** the lifecycle of requirements §7 (`domain.state_machines.CLARIFICATION`). Due date: two days before the clarification cut-off. A response is recorded against an issued clarification with its file kept as a tender document, and raises an impact task. The assessment may re-run takeoff and pricing, records what changed, and closes it as incorporated or no change.
  - **Grouping and export (FR-RFI-07):** issues on the same sheet and system, and unclear scope rows of a system, are proposed as one clarification; nothing is merged until a person confirms. The register exports to xlsx or docx in the company's form or a client layout.
  - **The only exit is a download.** The export is returned to the signed-in person and recorded as theirs. "Issued" is a person saying they sent it.
  - **Qualifications (FR-RFI-05):** "Prepare the submission" turns every unresolved clarification into a proposed qualification (asked, not settled) or assumption (never asked), linked back, for a person to accept or reject.
  - **UI:** a **Clarifications** page per bid (bid bar tab and bid page card): candidates, register, a panel per clarification with its actions, export, qualifications.
- **Key modules / files:**
  - `domain/state_machines.py`: `ClarificationState`, `CLARIFICATION`; `services/transitions.py` reads the guards' facts from the row.
  - Pure: `clarifications/drafting.py` (`Candidate`, `Draft`, `compose`, `engineering`, `propose_groups`), `clarifications/export.py`.
  - `services/clarifications.py` (`candidates`, `draft`, `edit`, `draft_with_model`, `transition`, `design_approval`, `record_response`, `assess_impact`, `export_register`, `prepare_submission`, `decide_qualification`).
  - `api/clarifications.py`: `/bids/{id}/clarifications…`, `/bids/{id}/qualifications…`.
  - Model path: `agents/clarification_drafter.ClarificationDrafter`, route `clarification_draft`, prompt `v1`.
  - Migration `0038`: `clarification`, `clarification_source`, `qualification`, all under RLS.
  - `config/clarifications.yaml`: lead time, engineering subjects and words, export templates. Marked "to be confirmed".
  - Frontend: `pages/ClarificationsPage.tsx`; route, bid bar and bid page card.
- **How to run and demo:**
  1. `make up`. Take a bid through the specification analysis (**Specification → Check against the drawings**) and, for variances, the client BOQ mapping.
  2. Set the bid's **Clarifications close** date on the bid page.
  3. Open **Clarifications**. Under **To raise**, **Draft** one, or **Confirm the group and draft one**.
  4. **Open** it: edit the query, **Send for internal review**. As Design Manager, **Approve as Design Manager** (if flagged). As Bid Manager, **Approve to issue**.
  5. **Download the register**, send it yourself, then **Record as issued**.
  6. Record the response with its file; **Assess the impact** and close.
  7. **Prepare the submission**: unresolved ones appear as proposed qualifications.
- **Requirement IDs covered (test names):**
  - FR-RFI-01: `tests/clarifications/test_clarifications.py::test_the_model_reserves_construction_rfis_and_nothing_else`; `tests/db/test_clarifications.py::TestTenderClarificationsOnly`; `Clarifications.test.tsx`.
  - FR-RFI-02: `tests/clarifications/test_clarifications.py::TestDraft`; `tests/db/test_clarifications.py::TestDrafting` (a seeded spec conflict and a bill variance each draft with every field and evidence; no evidence, no draft, in the service and the database; the model rewords only when it cites the draft's evidence); `Clarifications.test.tsx`.
  - FR-RFI-04: `tests/clarifications/test_clarifications.py::TestLifecycle`; `tests/db/test_clarifications.py::TestRegister` (due dates follow the cut-off; a response links back, raises a task and closes after the impact is assessed), `::TestApi`; `Clarifications.test.tsx`.
  - FR-RFI-05: `tests/db/test_clarifications.py::TestQualifications`; `Clarifications.test.tsx`.
  - FR-RFI-06: `tests/clarifications/test_clarifications.py::TestOptionsAndEngineering`; `tests/db/test_clarifications.py::TestApproval`, `::TestApi`; `Clarifications.test.tsx`.
  - FR-RFI-07: `tests/clarifications/test_clarifications.py::TestGroupingAndExport` (groups; headings and cells match the template, xlsx and docx; no module imports anything that could send); `tests/db/test_clarifications.py::TestGroupingAndExport` (merged on confirmation; the export is a recorded download and no job is queued); `Clarifications.test.tsx`.
- **Deviations and decisions:**
  - **No plan-and-approve pause,** as the owner prefers.
  - **Rules draft, the model rewords.** The prompt names a drafting agent. Here the draft is composed by rule, so every field and citation is deterministic, and the model is asked by a person only to improve the wording. Its output must cite evidence by number; an answer citing none fails output validation and nothing is saved.
  - **A "client's template" is a configured layout** (headings and the field under each), not an uploaded file, since no client template has arrived. One sample client layout is shipped.
  - **Potential cost and programme impact are fields the estimator fills.** A quantity variance states the quantity difference; nothing else is estimated by the platform.
  - **"Missing-information flags" are the specification issues of category "missing"**, offered as the source kind `missing_information`.
  - **The Design Manager's approval is a recorded act of its own,** not a state: the lifecycle of §7 has no state for it.
  - **Conversion at submission is asked for by the Bid Manager.** It is not tied to G4, which is not built yet.
  - **Editing a draft re-checks its engineering content;** new engineering content clears an earlier Design Manager approval.
- **Manual checks and results:**
  - Not run through the browser on the local stack. The page is covered by component tests and the API by database tests.
- **Defects found and fixed during the step:**
  - Response models named like existing ones (`TransitionIn`, `TemplateOut`, `EditIn`, `GroupOut`, `SheetOut`) changed those models' generated names; they are prefixed now.
  - A forward reference in a route's response model stopped the OpenAPI schema from being generated.
- **Known gaps and follow-ups:**
  - **The lead time, engineering words and templates are placeholders** for the Bid Manager and Design Manager to confirm; clients' real templates are a business-track input.
  - **Level and grid are filled only where an issue carries them;** most specification issues cite a sheet, not a grid.
  - **Bill variances are not grouped,** only issues sharing a sheet and system or a system's scope rows.
  - **The impact assessment re-runs takeoff and pricing for the whole bid,** not only what the response touches; a response that brings revised drawings goes through the addendum flow (P2-02).
  - **Deadline alerts for clarification due dates** use the existing cut-off alerts; there is no per-clarification reminder.
  - **Clarifications from coordination issues are FR-RFI-03 (P3).**
  - **Accepted qualifications are listed, not yet assembled into the tender package** (FR-PKG-04, P4).

## P2-07 · Bid Risk and Qualifications · 2026-10-04

- **Summary:** the bid team sees scope gaps, design-responsibility exposure and execution risk, each with its evidence, a treatment and an impact, and the list of what the offer is qualified by, every entry tied to its source.
  - **Scope-gap checklist (FR-RSK-01):** eleven items per system (pumps, tanks, breeching inlets, hydrants, hose reels, hydraulic calculations, shop drawings, testing and commissioning, authority inspections and FSC support, builder's works, power supply interfaces), from `config/risk.yaml`. Each is pre-filled from the scope matrix (an interface row, or obligation rows that agree) or the takeoff (item types counted). What nothing settles is open. A person resolves each as included, excluded, by others or clarified; a status other than the one proposed needs a reason. A rebuild keeps what a person decided.
  - **Design responsibility (FR-RSK-02):** sentences that put design and build, shop drawings, hydraulic calculations or the engagement of a Qualified Person on the contractor, one risk a kind, citing every clause. Each has a proposed treatment.
  - **Execution risks (FR-RSK-03):** basement levels (from the drawings and the takeoff), work at height (a ceiling height above 3,000 mm), high-rise (levels served), and night work, occupied building, shutdowns, congested ceilings and restricted access from the words of clauses and drawing notes. Each cites its evidence.
  - **Impact (FR-RSK-04):** an execution risk that a labour multiplier describes is valued with the labour engine: the baseline hours it reaches times what the multiplier adds, at the trades' rates. A multiplier already confirmed in the labour estimate adds nothing more. Design risks are not computed. The estimator accepts the figure, or sets their own with a reason.
  - **Qualifications (FR-RSK-05):** proposed from risks (qualify → qualification; priced → assumption), checklist items and scope rows that are excluded or by others (exclusions), and the measurement conventions (assumptions); P2-06 adds those from unresolved clarifications. Every entry names its source, enforced in the database. Entries are edited with a history, and a person may add their own (a deviation, say), linked to a source.
  - **Register (FR-RSK-06):** treatment (price, qualify, clarify, accept), owner and status per risk, every change in the audit trail. A risk no longer found is marked, not deleted.
  - **G3 readiness:** the checklist built, every item resolved, every risk treated. The bid's "approve (G3)" transition now refuses until then, and the Risk page shows it.
  - **UI:** a **Risk** page per bid (bid bar tab and bid page card).
- **Key modules / files:**
  - Pure: `risk/rules.py` (`checklist`, `design_risks`, `execution_risks`), `risk/impact.py`.
  - `services/risk.py` (`build_checklist`, `resolve_check`, `find`, `computed_impact`, `decide_impact`, `treat`, `propose_qualifications`, `add_qualification`, `edit_qualification`, `history`, `g3_readiness`).
  - `api/risk.py`: `/bids/{id}/risk…`.
  - `domain/state_machines.py`: a guard on "approve (G3)"; `services/transitions.py` reads readiness.
  - Migration `0039`: `scope_check`, `risk` (under RLS); `qualification.source_kind`, `source_ref`, `source_label`, and the kinds `exclusion` and `deviation`.
  - `config/risk.yaml`: the checklist, the wording rules, thresholds. Marked "to be confirmed".
  - Frontend: `pages/RiskPage.tsx`; route, bid bar and bid page card.
  - Fixture: `synthetic_spec.RISK_CLAUSES`, `with_risks()`, `EXPECTED_DESIGN_RISKS`, `EXPECTED_WORDING_RISKS`.
- **How to run and demo:**
  1. `make up`. Take a bid through the specification analysis so the scope matrix exists, and build its BOQ with a productivity library loaded.
  2. Open **Risk**. **Build the checklist**: items settled by the scope matrix are filled in; resolve the rest.
  3. **Find the risks**: each shows its clause, drawing note or parameter. Set a treatment and owner.
  4. On an execution risk, **Accept the computed impact**, or enter an allowance and a reason and **Adjust**.
  5. **Propose from risks and scope**: edit an entry's wording, open its **History**, accept or reject it.
  6. The banner turns to "Ready for G3" once nothing is open.
- **Requirement IDs covered (test names):**
  - FR-RSK-01: `tests/risk/test_risk.py::TestChecklist`; `tests/db/test_risk.py::TestChecklist` (pre-filled from the scope matrix; G3 refused until every item is resolved, then approved; a resolution survives a rebuild), `::TestApi`; `Risk.test.tsx`.
  - FR-RSK-02: `tests/risk/test_risk.py::TestDesignResponsibility`; `tests/db/test_risk.py::TestDesignResponsibility`; `Risk.test.tsx`.
  - FR-RSK-03: `tests/risk/test_risk.py::TestExecution`; `tests/db/test_risk.py::TestExecution` (basement, high ceiling, night work, occupied building, shutdown, high-rise with evidence; a risk no longer found is kept); `Risk.test.tsx`.
  - FR-RSK-04: `tests/risk/test_risk.py::TestImpact`; `tests/db/test_risk.py::TestImpact` (night work at a fifth more hours and cost from the labour engine; an adjustment needs a reason, in the service and the database; a confirmed multiplier adds nothing more); `Risk.test.tsx`.
  - FR-RSK-05: `tests/db/test_risk.py::TestQualifications` (every entry's source resolves to an item of the bid; edited with history; a person's entry must name a source); `Risk.test.tsx`.
  - FR-RSK-06: `tests/db/test_risk.py::TestRegister`, `::TestApi`; `Risk.test.tsx`.
- **Deviations and decisions:**
  - **No plan-and-approve pause,** as the owner prefers.
  - **Rules only.** No model is used: every finding is a pattern over a sentence, a level name, or a parameter against a threshold.
  - **G3 is enforced, not only shown.** The prompt asks for a readiness check shown to the Commercial Director. The check is also a guard on the bid's G3 transition, so a bid with no checklist cannot be approved.
  - **"Contract text" is the specification.** Contract conditions are not read as clauses yet (FR-RSK-07 is P4), so design-responsibility risks come from specification clauses only.
  - **Impact is an allowance, not a range.** The labour engine gives one figure per multiplier; programme impact is given as extra man-hours.
  - **Checklist items unresolved are not risks.** They hold up G3 by themselves; excluded and by-others items become exclusions.
  - **History is the audit trail** of the entry or risk, not a separate table.
  - **One entry a source.** Proposing again leaves an existing entry, and its edited wording, alone.
- **Manual checks and results:**
  - Not run through the browser on the local stack. The page is covered by component tests and the API by database tests.
- **Defects found and fixed during the step:**
  - Three response models were named like existing ones and renamed those in the generated client; they are prefixed now.
- **Known gaps and follow-ups:**
  - **The checklist, wording rules and thresholds are placeholders** until the company's scope-gap checklist and risk-appetite guidance arrive.
  - **Level multipliers reach only lines billed by level** (P2-05's gap), so a basement or height risk is valued on the sprinkler heads of that level, not its pipework.
  - **Design risks have no computed impact;** the estimator enters an allowance.
  - **Shutdowns have no labour multiplier,** so their impact is not computed.
  - **Risks are found on request,** not when a document or parameter changes.
  - **Accepted allowances are not yet added to the cost build-up;** contingency there is still entered by the estimator.
  - **The owner is a name typed in,** not chosen from the bid's team.
  - **Contract-term review is FR-RSK-07 (P4).**

## P2-08 · Review Pack, Gates G2–G4, Submission Freeze and Outcomes · 2026-10-04

- **Summary:** approvers get a review pack and sign G3 and G4 in the platform; G4 freezes the submission; outcomes are recorded; the libraries change only through approved proposals.
  - **Review pack (FR-PKG-01):** eight sections from what the platform already holds: estimate by component, estimate by system, key cost drivers and margin, risk allowances with treatments, top variances against the client's bill, open clarifications and issues, unpriced lines, G1 coverage. Each names the page it comes from. As a workbook (a summary sheet and a sheet a section) and a PDF.
  - **Gates (FR-PKG-02):** G1 and G2 were built earlier (Senior Estimator). G3 (Commercial Director) needs G2, a resolved scope checklist, treated risks and a bid under review; it records approver, time, comment and the hash of the estimate, and moves the bid to "approved for submission". G4 (Commercial Director) needs G3, no unresolved clarification and no undecided qualification; it freezes the submission, records the manifest's hash and moves the bid to "submitted".
  - **No transmission.** After G4 the frozen files are offered for download to a person on the bid, and each download is audited. Nothing sends a bid anywhere.
  - **Freeze (FR-PKG-03):** a manifest lists every record the submission rests on (takeoff items, evidence, bill and lines, rates used, quotations, build-up entries, conventions, labour conditions, clarifications, qualifications, risks, checklist, approvals), each with its version and a content hash, and five exported files with theirs. Manifest and files are written once to the snapshot store. The snapshot row is append-only in the database. `verify_snapshot()` reads everything back and checks each hash, and lists records changed in the platform since.
  - **Outcome (FR-LRN-02):** awarded (with the price where known), lost or withdrawn, with reasons and competitor feedback; the bid's lifecycle follows. `GET /outcomes` reports submitted, won, lost, awaiting, win rate and value won.
  - **Library governance (FR-LRN-03):** a change to the rate or productivity library is proposed (from a quotation, an outcome or an estimator) and changes nothing. The Senior Estimator approves it, which applies it as a new version, or rejects it with a reason.
  - **UI:** a **Review** page per bid (gates, frozen submission, outcome, the pack); the Rates page gains the proposals queue.
- **Key modules / files:**
  - `services/review_pack.py` (`build`, `as_workbook`, `as_pdf`).
  - `services/submission.py` (`gate_status`, `g3_blockers`, `g4_blockers`, `approve_g3`, `approve_g4`, `freeze`, `verify_snapshot`, `submission_file`, `record_outcome`, `outcome_report`).
  - `services/library_governance.py` (`propose`, `decide`).
  - `api/submission.py`: `/bids/{id}/review-pack`, `/gates`, `/submission`, `/outcome`; `/outcomes`; `/library-proposals`.
  - Migration `0040`: `submission_snapshot` (append-only trigger, RLS), `bid_outcome` (RLS), `library_proposal`.
  - `storage/object_store.py`: `get_snapshot_store()`; settings `s3_snapshot_bucket`, `s3_snapshot_lock_days`.
  - Frontend: `pages/ReviewPage.tsx`, `pages/LibraryProposals.tsx`.
- **How to run and demo:**
  1. `make up`. Take a bid through G1 and G2, the risk page (checklist resolved, risks treated) and move it to "under review".
  2. Open **Review**. The pack shows the estimate; download it as PDF or Excel.
  3. As Commercial Director: **Approve G3**. Accept or reject the qualifications, then **Approve G4 and freeze the submission**.
  4. The page shows the snapshot verifying and offers its files. Record the **Outcome**.
  5. On **Rates**, propose a rate as an estimator; approve it as Senior Estimator and see version 2.
- **Requirement IDs covered (test names):**
  - FR-PKG-01: `tests/db/test_submission.py::TestReviewPack` (every section; figures equal the build-up, the bill totals and the labour estimate; workbook and PDF with every section and link); `Review.test.tsx`.
  - FR-PKG-02: `tests/db/test_submission.py::TestGates` (G3 blocked with reasons, approvable only by the Commercial Director, records the hash; G3 and G4 refuse every other role through the API; G4 waits for qualifications; no module can send, and no job is queued); `Review.test.tsx`.
  - FR-PKG-03: `tests/db/test_submission.py::TestFreeze` (the snapshot verifies; a changed file, a missing file and a changed manifest each fail; UPDATE and DELETE on the snapshot row are refused; a later change to a record is listed); `Review.test.tsx`.
  - FR-LRN-02: `tests/db/test_submission.py::TestOutcome`; `Review.test.tsx`.
  - FR-LRN-03: `tests/db/test_submission.py::TestLibraryGovernance` (no effect until approved; a new version afterwards; a rejection leaves the library as it was; the database refuses an applied entry on an unapproved proposal); `Review.test.tsx`.
- **Deviations and decisions:**
  - **No plan-and-approve pause,** as the owner prefers.
  - **Object lock is configuration.** Locally the snapshot goes in the main bucket, written once (`put_once`). With `s3_snapshot_bucket` set, objects go to that bucket, and with `s3_snapshot_lock_days` each is put under a compliance-mode lock. In production the bucket's own retention lock governs (ADR-008). The lock itself is not exercised by a test: SeaweedFS's support was not verified.
  - **"Writes to snapshot records are rejected"** is the append-only trigger on `submission_snapshot`. The records the manifest lists are not frozen row by row; a change to one after submission is reported by `verify_snapshot` as changed since.
  - **G3's hash is of the estimate** (the pack's figures, the bill and the qualifications); G4's is the manifest's.
  - **The review pack's PDF is plain tables** drawn with matplotlib, the PDF library already in the project.
  - **Outcomes stand once recorded;** the price, reasons and feedback can be added to.
  - **Proposals are made by people.** Nothing proposes one automatically yet; a confirmed quotation still becomes a rate directly, by the Senior Estimator's confirmation.
  - **Decision D5 (named approvers) is open:** the gate roles are those of the permission matrix.
- **Manual checks and results:**
  - Not run through the browser on the local stack. Pages are covered by component tests and the API by database tests.
- **Defects found and fixed during the step:** none beyond lint and type findings while writing.
- **Known gaps and follow-ups:**
  - **The priced client workbook is not among the frozen files;** the company bill, the review pack, the qualifications and the clarification register are.
  - **Accepted risk allowances are still not in the cost build-up** (P2-07's gap), so the pack shows them beside the estimate, not in it.
  - **The pack has no trend or comparison with earlier bids.**
  - **G0 (bid/no-bid) support is P4.**
  - **The full tender package (FR-PKG-04) is P4.**

## P2-09 · Phase 2 Hardening and Exit Evaluation · 2026-10-04

- **Summary:** the Phase 2 KPIs are instrumented, the exit report is generated, the Phase 1 suites and the non-functional benchmarks were rerun with Phase 2 in place, and the new surfaces were reviewed for security and privacy. **Gate recommendation: not ready to pass the gate; ready for the assisted-mode pilot.** No exit criterion can be judged without live tenders and a turnaround baseline.
  - **Tender turnaround:** working days (weekends and configured holidays left out) from the day a bid is opened to its first G3 approval, averaged over bids, against `phase2.baseline_turnaround_working_days` in `config/kpi.yaml`. With no baseline the reduction is not measured, rather than zero.
  - **Price provenance:** priced lines of the current bill with a library rate, a quotation or a named allowance, over priced lines. The database already refuses a priced line with neither, so the measure reads 100% wherever a bill is priced.
  - **Clarification acceptance:** when a clarification is drafted its wording is kept (`drafting.drafted`); when it is issued, the word-level edit distance between the two, over the longer, is recorded (`issued_edit_ratio`). At or under `clarification_minor_edit_ratio` (0.20) it counts as issued with minor edits.
  - **Where they show:** `GET /kpis/phase2`, a Phase 2 section on the KPIs page, and the exit report.
  - **Exit report:** `make exit-report-p2` writes `docs/reports/phase2-exit.md` from the measures, the regression runs, the evidence files, requirement coverage and `docs/reports/phase2-gaps.yaml` (pilot findings, reviews, known gaps, recommendation). `LIVE=1` adds every bid's measures.
  - **Retention:** the kept lines of a quotation file are cleared 365 days after its bid is lost, withdrawn or no-bid; the fields read from them and the file stay.
- **Key modules / files:**
  - `kpi/phase2.py` (`working_days`, `edit_ratio`, `minor_edits`, `provenance`, `reduction`).
  - `services/kpis.py` (`bid_phase2`, `phase2_kpis`); `api/kpis.py` (`/kpis/phase2`); `services/clarifications.py` (`_record_edits`).
  - `evals/p2_exit.py`, `firebid-eval exit-p2`; `evals/p2_workload.py`; Makefile targets `exit-report-p2`, `p2-benchmark`.
  - `scripts/load_test.py`: the users also open the cost build-up, labour, clarifications, risk, review pack and gates.
  - `services/retention.py` (`_clear_quotation_lines`); `config/retention.yaml`; `config/kpi.yaml` (`phase2`).
  - `docs/reports/phase2-exit.md`, `phase2-gaps.yaml`, `phase2-security-review.md`.
- **How to run and demo:**
  1. `make p2-benchmark`, then `make exit-report-p2` (in the Dev Container). Open `docs/reports/phase2-exit.md`.
  2. With the stack up: `make exit-report-p2 LIVE=1` for the measures of every bid; the KPIs page shows the same for the bids a person is on.
  3. From the host, with the stack up: `uv run --project backend python scripts/load_test.py` and `scripts/pipeline_benchmark.py --synthetic 50` (they reach the stack on `localhost`).
- **Requirement IDs covered (test names):**
  - NFR-14: `tests/kpi/test_phase2.py` (working days, edit ratio, provenance, reduction); `tests/db/test_phase2_kpis.py` (turnaround from G3; the database refuses an unsourced price; acceptance from a draft issued as drafted and one rewritten; a clarification with no draft on record is not measured; the API); `tests/evals/test_p2_exit.py` (pending without a pilot; meets and misses; no baseline; a regression becomes a gap; coverage); `Kpis.test.tsx`.
  - NFR-01, NFR-02: `tests/evals/test_p2_exit.py` (the Phase 2 workloads inside the 2 s budget; evidence recorded before Phase 2 is not counted as rerun).
  - NFR-07: `tests/db/test_phase2_kpis.py::TestQuotationLinesRetention`.
  - `make req-coverage PHASE=P2`: **106 of 108.** FR-DSN-05 and FR-DSN-06 have no test because they are not built (FR-DSN-06 partly); both are in the gap list.
- **Regression and benchmarks, 4 Oct 2026, local stack rebuilt on this branch:**
  - Phase 1 detection: no regression against the result of 28 Sep (sprinkler count 100%, pipe length error 0.002%). Title block reading and bill mapping 100%. Phase 2 systems: no regression against its baseline. `make eval-gate`: no regressions.
  - Load test, 10 bids and 20 users, with the Phase 2 pages: 2,092 requests, none failed, p95 0.156 s (was 0.094 s without them).
  - Ingestion: 50 sheets in 4.7 min, 300 projected at 28.4 min against 60 (was 23.2). First-pass takeoff of 50 sheets: 4.9 min against 240.
  - Phase 2 workloads in process (5,000 bill lines, 500 clarification candidates, 2,000 clauses): the slowest, the labour estimate, about 0.5 s against 2 s.
  - Restore drill: 3,455 MB restored and verified in 1.2 min; 83 tables and 151 audit chains match. Game day: 12 routes fall back, 1 escalates, 0 fail. Deployment guard refuses a window near a deadline.
  - `pip-audit`, `npm audit` and `bandit`: nothing found.
- **Deviations and decisions:**
  - **No plan-and-approve pause,** as the owner prefers.
  - **"Submission-ready" is read as the first G3 approval,** and "receipt" as the day the bid was opened: the bid has no received date.
  - **Price provenance is measured on the current bill,** not frozen at G2: the constraint that holds it applies at all times.
  - **Edit distance is by word,** case and spacing ignored, over the subject, the problem and the options.
  - **The load test reads the Phase 2 pages; it does not price or draft on each bid.** Scale is measured in process instead.
  - **No pilot evidence exists,** so every criterion is marked pending, as the step allows.
  - **Cost and provider review:** nothing to compare. No provider key exists and `compare-models` scores a stand-in. No route change is proposed.
- **Manual checks and results:**
  - The stack was rebuilt on this branch (migrations 0038 to 0040 applied) and the benchmarks above run against it. The pages were not walked through in a browser.
- **Defects found and fixed during the step:**
  - Quotation file lines were kept for ever (now cleared by retention); `bid_outcome.competitor_feedback` was missing from the data inventory.
- **Known gaps and follow-ups:** see the gap list in `docs/reports/phase2-exit.md`. The largest: no pilot and no baseline; CI not running (account billing); no model route run against a real provider (D2); FR-DSN-05 and 06; integration and end-to-end tests not run for P2-06 to P2-09; two open medium privacy and integrity findings (free text in the audit log; object lock unconfigured locally).

## Platform · Dependencies, Python 3.14 and container images · 2026-10-04

- **Summary:** outside the build steps, `main` moved to Python 3.14 with refreshed dependencies and development images, each tested locally because CI is not running.
  - **Dependencies (#46):** about 30 packages refreshed, SQLAlchemy 2.0 to 2.1 among them. Three annotations in `api/audit.py` changed to `Select[AuditEvent]`, as SQLAlchemy 2.1 writes them.
  - **Python 3.14 and uv 0.12.23 (#47):** `requires-python`, `.python-version`, the ruff and mypy targets, the lock file and the backend, sandbox and Dev Container images. ADR-001 and the project context record why the 3.12 pin no longer holds. The formatter's 3.14 target writes `except A, B:` without parentheses.
  - **Images (#50):** Keycloak 26.8, ClamAV 1.5.4, SeaweedFS 4.48. SeaweedFS 4.48 exits non-zero when a bucket already exists, which stopped a stack with data from starting; `s3-init` now treats that as success.
  - Renovate's #12 and #13 were merged first and left `main` inconsistent; they were reverted (#45) and their content came back in #47 and #50.
- **Defects found and fixed:** a timing test (`tests/drawings/test_touching.py`) compared one run with one run and failed on 3.14 when a pause landed in the first; it takes the best of three.
- **Verification, local, on 3.14:** full `make check` (2,181 backend, 122 frontend) with that one timing failure, since fixed; `make test-integration` 10 passed; `make e2e` 10 passed, 1 skipped; load test 1,689 requests, none failed, p95 0.106 s. The stack and the Dev Container were rebuilt on the 3.14 images.
- **Known gaps and follow-ups:**
  - A 3.14 test run emits about 135,000 deprecation warnings, nearly all ezdxf setting a NumPy array's shape (NumPy 2.5).
  - `make bootstrap` runs before git trusts the mounted repository, so a Dev Container rebuild fails at its setup step until `git config --global --add safe.directory` is run by hand.
  - `make e2e` expects a database with no load-test bids: the load test adds the estimator account to every bid it creates.

## FR-DSN-05, FR-DSN-06 · Layout export and match lines · 2026-10-05

- **Summary:** the two Phase 2 requirements no build step had covered. Requirement coverage for Phase 2 is now 108 of 108.
  - **Match lines (FR-DSN-06):** when a sheet's design basis is read, its match lines are found: a text reading "MATCH LINE", and the straight line beside it that runs across the plan (dashed or not; a line of the structural grid only when nothing else is beside the label). The sheet it names ("SEE DWG FP-L10-02") is kept. The sheet's side is proposed as the side with more of the pipework found drawn; failing that, more coloured linework; failing that, the larger side; and the reason is shown. The sheet's scope becomes the plan on its side of every line, so the layout stops there and a shared floor is designed once.
  - **A person decides.** On the Design page each line shows its words, the sheet it continues on and why that side; a person may take the other side, use the whole sheet, or go back to the proposal. A person's choice is kept when the sheet is read again, and a change lays a confirmed sheet out again. A label whose line was not found is named and limits nothing.
  - **Export (FR-DSN-05):** a confirmed sheet's layout downloads as a PDF or a DXF: the tender drawing in grey, drawn from the sheet's extracted geometry; the proposed heads, range pipes and feeds with their sizes in colour; the scope outline where match lines limit it. Both are stamped "For estimation only: not for construction" across the sheet and in a note with the criterion and its source, the design rules version, who confirmed, the counts and when it was made. What a person rejected is left out. It is a download for a person on the bid, audited; nothing is sent.
- **Key modules / files:**
  - `design/match_lines.py` (`find`, `scope`, `scope_from_json`); `design/export.py` (`as_pdf`, `as_dxf`, `STAMP`).
  - `services/design.py`: `_read_match_lines`, `choose_sides`, `follow_match_lines`, `export_layout`.
  - `api/design.py`: `PUT .../design/sheets/{id}/scope` takes `sides` or `follow_match_lines` as well as a polygon; `GET .../design/sheets/{id}/export?format=pdf|dxf`.
  - Migration `0041`: `sheet_design.match_lines`, `sheet_design.scope_source`.
  - `evals/synthetic_design.design_intent_plan(match_line=True)`; `pages/DesignPage.tsx`.
- **How to run and demo:**
  1. `make up`. On a bid with a design-intent sheet, open **Design** and **Read the design basis**.
  2. Open a sheet: its match lines are listed with the side proposed. **Take the other side** or **Use the whole sheet** to change it.
  3. Confirm the criterion; once the layout is proposed, **Download the layout as PDF** or **as DXF**.
- **Requirement IDs covered (test names):**
  - FR-DSN-06: `tests/design/test_match_lines.py` (found from its label; the side by services, by pipework, by size; a sheet between two lines; a line at an angle; a grid line not mistaken for it; a label with no line; the spaces keep to the side); `tests/db/test_design_match_lines_and_export.py::TestMatchLines` (scope proposed with its words; the layout keeps to the side, and the two sides make the whole; a person's side kept on reading again; the API); `Design.test.tsx`.
  - FR-DSN-05: `tests/design/test_export.py` (the PDF's size, stamp and words; the DXF's layers, head positions and stamp); `tests/db/test_design_match_lines_and_export.py::TestExport` (the stamp and what the layout rests on; a rejected head left out; the scope drawn; refused before a layout; a download for a member, audited, not found for another bid); `Design.test.tsx`.
- **Deviations and decisions:**
  - **No plan-and-approve pause,** as the owner prefers. No build prompt exists for these two; the requirements' own text was the brief.
  - **The side is a proposal, not a reading.** A drawing does not say which side of a match line is the sheet's own; the platform says why it proposes one and a person can change it.
  - **The export redraws the tender drawing** from extracted geometry, as the legend crops do, so it is made the same way for a PDF sheet and a DXF sheet and shows what the platform read.
  - **The DXF is in sheet millimetres, y up,** on layers `TENDER-DRAWING`, `PROPOSED-HEADS`, `PROPOSED-PIPE`, `PROPOSED-SCOPE` and `ESTIMATION-ONLY`.
- **Manual checks and results:** not run on a real tender, and the page was not walked through in a browser.
- **Defects found and fixed during the step:** the first finder measured from a label's middle and took the longest line near it, which on the synthetic sheet was a structural grid line; it now measures from the label's box and sets grid lines aside.
- **Known gaps and follow-ups:**
  - **Synthetic sheets only:** see the gap list in `docs/reports/phase2-exit.md`.
  - **A stepped match line** is followed as one straight line.
  - **A DXF whose pipes take their colour from their layer** gives no colour to judge the side by; the extractor does not resolve layer colours.
  - **Two sheets are not compared:** nothing checks that the sheets either side of a line took opposite sides.
  - **The synthetic design sheet finds no pipe run** (it has no legend), so the database tests exercise the larger-side rule; the pipework rule is tested in `tests/design`.

## Trial · FR-DSN-05, 06 on the MOH tenth-storey sheets · 2026-10-05

- **Summary:** the four tenth-storey sheets of the MOH design-intent set (`A03-10-01` to `04`) were uploaded to the local stack, and run through the design modules from their extracted geometry. The new features work on the real geometry after three fixes; the stack does not yet take these sheets from upload to a layout.
- **Defects found and fixed:**
  - **The match line's words.** These sheets write "FOR CONTINUATION, REFER TO DRG. NO. -" along the line, with the drawing number as a separate text beside it; none says "MATCH LINE". The finder now takes a continuation note as a label and reads the sheet from the number written beside it.
  - **The match line's line.** On a real plan dozens of lines pass every label, and the finder took a wall crossing the note. It now takes a line running the way the note is written before one crossing it, and a line of regular dashes before a solid one. The synthetic sheet writes its label along the line, as these do.
  - **View detection crashed** (`drawings/scale.py`): a "0" beside a line was paired with it as a dimension, implying 1:0, and comparing two of them divided by zero; the sheet got no views. A figure of nought is no longer evidence, and `same_scale` no longer divides by it.
  - **Words written up the sheet** came out upside down in the export.
- **Results on the real geometry:** each of the four sheets gives one match line, the dashed one, naming its neighbour (01 with 02, 03 with 04), in about a second. Sheet 1 at the printed 1:100: 514 heads whole, 372 on its own side; the estimators counted 496 and 346. Its PDF, viewed, shows the plan in grey, the heads, pipes and sizes, the scope outline at the match line, and the stamp.
- **Not fixed, and blocking these sheets on the stack:** see the gap list in `docs/reports/phase2-exit.md`: title blocks not read (no Current sheet), two sheets not classed as plans, no dimension to verify a scale by, and an export of 11 MB (PDF) and 273 MB (DXF) taking a minute or more.
- **Tests:** `tests/design/test_match_lines.py` (a continuation note; the number beside it; the dashed line along the note, not the wall through or beside it), `tests/design/test_export.py` (words written up the sheet), `tests/drawings/test_views.py` (a figure of nought). 1,515 non-database tests pass. The full suite was not rerun.

## Fix · Title blocks and the layout export on a real tender · 2026-10-05

- **Summary:** what stopped the MOH tenth-storey sheets on the stack, and made their export impractical. Two of the four sheets now go from upload to a layout and an export on the stack.
- **Title blocks (FR-DOC-02):**
  - **The page's origin.** These PDFs put the origin of the page at its centre. `parsing/text.pdf_text` placed text from the origin, so every word was read half a sheet from where it is drawn and the title block was looked for where it was not: three sheets got no register entry and one was read as "T/B". Text is now placed from the crop box, as `geometry_pdf` places linework.
  - **A number built to a naming standard** (`60743399_ACM_TN_TO_D_MFP_A03-10-01`, seven parts after an eight-figure project number) did not fit the drawing number's pattern, and the DATE label above took it as its date. The pattern allows up to eight parts and a ten-figure project number, and a value that reads as a drawing number is no longer taken by another field's label.
  - On seven sheets of the set (the tenth storey's four, and one each from B1, the helipad and level 1), the number and the revision are read from their labels at 0.97; on the stack the four tenth-storey sheets are Current without a person.
- **Layout export (FR-DSN-05):**
  - **Smaller and quicker.** The tender drawing is drawn a primitive at a time, not a piece at a time, and the DXF is written as R12, entity by entity as it goes. On sheet 1: the PDF from 10.8 MB in about 60 s to 8 MB in about 21 s; the DXF from 273 MB in about 85 s to 110 MB in about 11 s.
  - **Not inside the request.** `POST .../design/sheets/{id}/export` asks for the file; the `design.export` job makes it and keeps it in the object store; `GET .../export/status` says where it stands; `GET .../export` hands out the kept file. A DXF is kept compressed and unpacked by the browser: 110 MB travels as about 4 MB.
  - **Stale files are not handed out.** Each file carries a stamp of the layout it was made from (when it was laid out, its scope, criterion and rules, and how many heads and pipes a person has left in); a file made before any of that changed is refused, and asking again makes a new one.
  - The download, not the making, is what is audited, in the name of the person who took it.
- **Key modules / files:** `parsing/text.py`; `drawings/title_block.py` (`DRAWING_NUMBER`, `_value_for`); `design/export.py` (`_tender_lines`, `as_dxf` on `ezdxf.addons.r12writer`); `services/design.py` (`layout_stamp`, `exports_of`, `request_export`, `make_export`, `export_file`); `jobs/tasks.py` (`design.export`); `api/design.py`; migration `0042` (`sheet_design.exports`); `pages/DesignPage.tsx`.
- **On the stack, 5 Oct 2026, sheets `A03-10-01` and `A03-10-04`:** read and Current in under two minutes; each calibrated by hand at the printed 1:100 (the trial's assumption); design basis read with the match line and the sheet's side; 328 and 199 heads proposed; each export ready about 20 s after it was asked for.
- **Requirement IDs covered (test names):**
  - FR-DOC-02: `tests/drawings/test_title_block.py::TestANamingStandardNumber`, `::test_text_is_placed_on_the_sheet_when_the_page_s_origin_is_not_its_corner`.
  - FR-DSN-05: `tests/db/test_design_match_lines_and_export.py::TestExport` (asked for, made by a job, kept, downloaded; a stale file refused; nothing queued for an empty layout); `tests/design/test_export.py`; `Design.test.tsx`.
- **Known gaps and follow-ups:** see the gap list: two of the four sheets are not classed as plans; a plan with no dimension needs a person's calibration; the DXF is 110 MB once unpacked.

## Fix · View titles on a real sheet · 2026-10-05

- **Summary:** two of the four MOH tenth-storey sheets were classed as a schematic and a detail, not a plan, so the design step passed them by. All four are now one plan view each.
- **Cause:** a view is known by its title, and a title was any text holding a title's word. The sheets' general notes say "... THE SCHEMATIC LAYOUTS, EQUIPMENT SIZES ..." and "... BIM MODELING FOR DETAIL": two "titles". Where the notes sat inside the area taken for the title block they were ignored (sheets 1 and 4); where they sat on the left they split the sheet in two (sheets 2 and 3). The sheet's real title, 87 characters long, was written by the PDF in two pieces cut inside the word LAYOUT, and was read on none of them.
- **Fix (`drawings/views.py`):**
  - `is_title`: a title is a phrase, not a sentence: nothing after a full stop, not a numbered note, at most 14 words, or 8 where it holds a comma.
  - `joined`: text written across the sheet in pieces on one line is put back together before titles are looked for.
  - `level_of` reads `10TH STOREY` as L10.
  - With the title found, the "1 : 100" printed beneath it becomes the view's stated scale without further change. It is still unverified: the sheet has no dimension to check it by.
- **On the stack, 5 Oct 2026:** the four sheets are read and Current, each one plan at a stated 1:100 on L10; calibrated by hand for the trial, each gets its design basis with the criteria its notes state, its match line, its neighbour and its side. Real schematic sheets of the same set are still classed as schematics.
- **Requirement IDs covered (test names):** FR-VIS-05: `tests/drawings/test_views.py::TestTitlesOnARealSheet` (a sentence of the notes is not a title; a title in two pieces is read as one; the sheet is one plan with its title, scale and level; a level before its word).
- **Tests:** 1,523 non-database tests pass. The database and frontend suites were not rerun: the change touches neither.
- **Known gaps and follow-ups:** four sheets of 148, one consultant; a plan with no dimension still needs a person's calibration.

## Trial and fix · 24 more sheets of the MOH set · 2026-10-06

- **Summary:** three sheets from each of eight levels (B1, B3, the helipad, L1, L06, L15, L21, the upper roof) were run through the stack. All 24 were read and Current with the right sheet number at 0.97. Two defects were found and fixed; one cause is left for a decision.
- **Defects found and fixed:**
  - **A verified scale reported as conflicting (`drawings/scale.py`).** Nine sheets had 13 to 26 grid dimensions agreeing with the stated 1:100 and were blocked, because a few figures beside a line were taken for dimensions: single figures (a grid bubble's number) beside a short line, and a dimension paired with the wrong line. A figure under 10 mm is no longer a dimension. Where at least five dimensions agree with the stated scale and they are at least four fifths of the evidence, the scale is verified, and the reason says how many figures were set aside; all the evidence is kept. A plan drawn at another scale than it states is still a conflict.
  - **A sheet that could not be read finished as if it had been (`services/parse_pipeline.py`).** `A03-06-03`, with nearly two million primitives, ran past the sandbox's memory. It was marked parsed, with no linework, no views and no failed sheet counted. It is now a failed sheet with the reason, and its title block is still read.
- **Not fixed:** the memory itself. Reading that sheet's linework takes about 1.6 GB against a 2 GiB address-space limit. See the gap list.
- **On the stack after the fixes:** the eleven other sheets of B1, B3, L1 and L06 are each one plan with a verified 1:100 and get a design basis with two or three match lines and the sheets they continue on; `A03-06-03` is shown as failed.
- **Also seen, not acted on:** the helipad and upper roof sheets each get a schematic view beside their plan; levels 15 and 21, like level 10, carry no dimension and need a person's calibration.
- **Requirement IDs covered (test names):** FR-VIS-05: `tests/drawings/test_views.py::TestFiguresThatAreNotDimensions`. NFR-01: `tests/db/test_parse_pipeline.py::TestFailure::test_a_sheet_whose_linework_could_not_be_read_says_so_and_keeps_its_title_block`.
- **Tests:** 1,527 non-database tests; the parse pipeline's failure tests. The rest of the database suite was not rerun.

## Fix · Memory on a heavy real sheet · 2026-10-06

- **Summary:** the whole MOH set (148 sheets) was run through the stack. At sheet 117 the parser pool was killed for memory and did not come back; four sheets had already failed for memory on their own. Reading a heavy sheet now takes a third of the memory, and the pool restarts if it is killed. With the fix, the remaining 31 sheets were read with no failure.
- **Cause:** two stages held every primitive of the sheet as a Python value a cell. A real A0 plan has up to two million primitives.
  - `drawings/geometry.Builder` collected all of them in Python lists before making the table.
  - `drawings/symbols.candidates` turned eleven columns of the whole table into Python values (`to_pydict`), and `geometry.segments` then copied every column of the table to read three.
- **Fix:**
  - `Builder` packs what it has collected into the table's own form every 50,000 primitives (`PACK_EVERY`); `len(builder)` and `builder.has(kind)` answer for all of them; a DXF viewport, which goes back over its own lines to cut them, collects `unpacked()`.
  - `geometry.kind_mask`, `shared_values` and `numbers` read a column without a Python value a cell: a boolean array, an array in which equal words are one object, floats with NaN where there is none. `symbols._Columns` gives finding symbols the same `columns[name][row]` from them; the loose shapes of a symbol's size are picked with arrays, not a row at a time.
  - `geometry.segments` copies only the columns it reads.
  - `infra/docker-compose.yml`: the sandbox restarts unless stopped.
- **Measured on `A03-06-03` (1.96 million primitives):** reading its linework 1,643 MB to 604 MB; finding its symbol shapes 2,134 MB to 1,214 MB; times unchanged. Under the sandbox's limits in the Dev Container, the four sheets that failed pass extraction, symbol shapes and rendering.
- **Requirement IDs covered (test names):** NFR-01: `tests/drawings/test_geometry.py::TestAHeavySheet` (packed as collected, the same table; a caller may go back over what it added; kinds, words and numbers read the same); `tests/drawings/test_symbols.py::test_candidates_are_the_ones_a_value_a_cell_found`.
- **Tests:** 1,531 non-database tests. The database suite runs in CI on this change.
- **Found by the same run, not fixed here:** finishing a document matches every symbol instance of the bid again. With 148 one-sheet files and 448,000 instances that is 148 full passes: the pool's memory crept to its limit and one document was rejected when the database could not sort them. See the gap list.

## Fix · Matching symbols on a tender of many files · 2026-10-06

- **Summary:** finishing a document matched every symbol instance of the bid by reading every instance's shape as text. On the real set (148 one-sheet files, 448,199 instances, each shape several kilobytes) that was three gigabytes held in the parser pool, once a document: its memory crept to its limit, and one document was rejected when the database could not sort the rows ("unexpected end of tape").
- **Fix (`services/symbols.match_instances`):** every instance is read once as a digest of its shape (`md5` of the text, worked out by the database), and a shape's own text only for the first instance of each shape, 2,000 shapes a statement (`READ_CHUNK`). Shapes are worked out in the order of their first instance, as before, so the matches and the groups of unexplained symbols are what they were.
- **Measured on the real bid:** one pass over 448,199 instances (56,774 shapes) in 41 seconds, peak 576 MB.
- **Requirement IDs covered (test names):** NFR-01: `tests/db/test_symbol_mapping.py::...::test_a_shape_s_text_is_read_once_a_shape_however_the_shapes_are_batched`; `test_matching_again_writes_nothing` (one statement reads every instance, as a digest).
- **Not fixed:** it is still one pass a document, and a plan's furniture is still kept as candidate symbols (about 3,000 a sheet). See the gap list.

## Fix · A floor with no dimensions is verified by the grid it shares · 2026-10-07

- **Summary:** on the real 148-sheet tender, 68 of 145 plans were blocked from design and measurement: they state 1:100 and carry no dimension, so each waited for a person to calibrate it (FR-VIS-05 trusts no stated scale that nothing confirms). They are drawn on the gridlines of the floors below, whose dimensions prove the scale, and so prove how far apart the gridlines are. That spacing is a known dimension.
- **Grid marks (`drawings/grids.marks`, `marks_in`):** a real plan's gridlines are dashed, so `detect` finds no grid. The bubbles still stand in rows: a row is at least three bubbles of one size on one line, each label once, all letters or all numbers, in order. Each row is kept apart (a skewed wing has rows of its own), a gridline with a bubble at each end is one mark, and a label at two places marks nothing. Kept on the view as `sheet_view.grid_marks` (migration `0043`); `views.DETECTOR_VERSION` is `2`.
- **The check (`drawings/scale.known_spacings`, `grid_evidence`, `corroborate`):** every view proved by its own dimensions, or calibrated by a person, makes the real distance between each two gridlines of a row known; two proved views that disagree about a pair prove nothing of it. An unverified view is verified when one row of its gridlines has at least three known spacings (`GRID_SPACINGS_AT_LEAST`) and every one agrees with its stated scale within 1%. Spacings along another row that disagree are set aside and said to be: on the real set they are the skewed wing's bubbles. The grid never makes a view conflicting. A view that states no scale ("AS INDICATED") is verified only where at least five of its own dimensions and such a row all give one scale.
- **Where it runs (`services/views.check_against_grid`):** across the bid each time a document finishes (`parse_pipeline.finish`), so the order the sheets are read in does not matter, and after a calibration, so one calibration serves every sheet on the same gridlines. Decided afresh each time from what each view's own dimensions said, which is kept under `own` in `scale_evidence`: a view goes back to unverified when the view that proved it is gone. A sheet of another document whose view changed is detected again (its scale is part of the detection fingerprint). The evidence names each pair of gridlines, their spacing and the sheet it is known from.
- **Measured on the real bid:** 68 unverified plans to 6 (62 verified by the grid, 139 of 145 measurable). The six have fewer than three known spacings in a row. Finding the marks reuses the bubbles the grid detector already found.
- **Requirement IDs covered (test names):** FR-VIS-05: `tests/drawings/test_grid_scale.py::TestGridMarks` (9), `::TestScaleFromASharedGrid` (11); `tests/db/test_sheet_views.py::TestScaleFromASharedGrid` (verified by another floor's grid; checking again changes nothing and a view no longer proved goes back; one calibration serves every sheet on the same gridlines; a calibration that contradicts proves nothing); `tests/db/test_parse_pipeline.py::test_a_sheet_read_later_proves_the_scale_of_one_read_before_and_it_is_detected_again`.
- **No API change:** the view's `scale_evidence` already carries the reason and evidence. `sheet_view.grid_marks` holds gridline labels and positions, no personal data.
- **Not fixed:** `grids.detect` still finds no grid on a real sheet, so there are no grid references on them (see the gap list). A bid read before this change has no marks until its views are detected again ("Read again", the next entry).

## Fix · Read again: a failed sheet, a refused drawing, out-of-date views · 2026-10-07

- **Summary:** on the real tender four sheets failed for memory and one drawing was refused when it was finished. After the fixes nothing in the product could read them again, and views found by an older detector stayed as they were (the grid check needs marks only the new one keeps); each took a script on the database. A failed sheet was also invisible: its drawing said "Ready".
- **What can be read again (`parse_pipeline.to_read_again`):** a drawing with a sheet that could not be read, and a tender document refused after it was scanned (while being read, or when finished). Never one refused when it was sent, quarantined or not yet scanned, and never one still being read. Also the sheets whose views were found by an older `views.DETECTOR_VERSION`.
- **The action (`parse_pipeline.read_again`, `POST /bids/{id}/documents/read-again`, `document.upload`):** the failed sheets are marked unread and each such document goes back through `parse.document`, which reads only what is unread and finishes the document again. Out-of-date views are found again by one `views.again` job for the bid, in the parser pool, from the geometry already stored: a sheet at a time, each committed, a failing sheet left as it was; then the bid's views are checked against the grid and the sheets whose scale changed are detected again. A calibration is carried over. Safe to ask twice (what is queued is being read; one job waits for the views). Recorded in the audit trail as "documents: read again" with the counts.
- **Progress (`GET /bids/{id}/progress`):** `unread_sheets` (file, page and reason of each sheet that could not be read) and `read_again` (how many documents, sheets and views the action would reach).
- **Frontend (`DocumentsPage.tsx`):** the unread sheets are listed with their reasons, and a "Read again" button is offered only when there is something for it to do, saying what it will read.
- **Requirement IDs covered (test names):** FR-DOC-01: `tests/db/test_parse_pipeline.py::TestReadAgain` (a sheet that could not be read is said and can be read again; a drawing refused when it was finished is read again and one never scanned is not; a drawing still being read is left to its jobs; views found by an older detector are found again by one job); `tests/db/test_document_api.py::test_reading_again_is_for_those_who_may_send_documents`; `Documents.test.tsx` (lists a sheet that could not be read and offers to read it again; offers to find views again after the detector changed, and nothing when all is read).
- **API client:** regenerated (`make api-client`).
- **Not built:** reading again one chosen sheet or drawing (it is the whole bid's failures at once), and detecting symbols again after a symbol-detector change.

## Fix · Grid references on real sheets: dashed gridlines and a skewed wing · 2026-10-07

- **Summary:** the grid detector wanted one long line ending at each bubble. A real plan draws every gridline as a chain line, so no grid was found on any of the 148 sheets of the real tender and nothing on them had a grid reference (FR-VIS-07). A wing of that building also stands at 38.4 degrees to the sheet, with gridlines of its own.
- **Finding a dashed line (`drawings/grids._dashed`, `_through`, `_along`):** tried only where the long-line method finds no grid, so a drawing it already read is read as before. A gridline is the dashes lying on one line through its bubble's centre, at any angle: the dashes aimed at the centre are binned by direction, the likeliest eight directions are each tried (far across a sheet much of the building is aimed at any point), and a line is kept when its dashes total at least 40 mm, cover 30% of the length they span, and begin within ten radii of the bubble. Only bubbles the size of those that stand in rows are grid bubbles (a sprinkler is a letter in a circle with a pipe through it); a label on two different lines is trusted on neither.
- **Which lines make the grid (`_families`):** lines of one kind (letters, or numbers) running within 10 degrees of each other, their labels in order across them, are a family; the grid is the two families square to each other (within 5 degrees) with the most lines. The main block's lettered lines are so not paired with the wing's.
- **A gridline may lean and end (`GridLine.slope`, `origin`, `low`, `high`):** its position is taken at the point asked about, and a point past its drawn ends (by more than 10 mm) is not beside it, so a point in the main block gets no wing reference. Stored after the label and position; a long line is stored as it was. Where both ways are lettered or both numbered the reference parts them with a stroke ("Grid AA/K").
- **No grid index from a dashed grid (`GridSystem.one_grid`, `index`, `box`):** such a grid may be one of several on the sheet using the same labels, and the takeoff counts two detections at one index once. So it names places and is not used to match sheets.
- **Speed:** the long-line search picked its candidates from every segment for every bubble (about 8 s on a 1.5 million segment sheet); the long square lines are now picked out once (under 1 s). Finding dashed lines adds at most 3.7 s on the heaviest sheet.
- **Measured on the real bid:** 0 of 148 sheets with a grid before, 88 now (38 square to the sheet, 50 the skewed wing); 60 show one way of gridlines only. `views.DETECTOR_VERSION` is `3`; "Read again" refreshed the bid's 148 sheets in 11 minutes, keeping every scale verdict (139 verified). The locate API then names points on real sheets ("Grid 4M–5N" on A03-16-02, "Grid 2O–3P" in the wing of A03-10-01, nothing for a main-block point there). That bid has no confirmed symbols, so no detected object to carry a reference yet.
- **Requirement IDs covered (test names):** FR-VIS-07: `tests/drawings/test_dashed_grid.py::TestDashedGridlines` (9: found in chain lines; a line knows how far it is drawn; a long line as it always was; references and no index; survives storage; a pipe through a sprinkler; one way is not a grid; a label on two lines; a bubble at each end), `::TestASkewedWing` (4: found at its angle; a point named by the lines either side; beside a square block; labels parted by a stroke), `::test_a_sheet_with_no_bubbles_has_no_grid`.
- **Not fixed:** see the gap list (no shared index; 60 sheets with one way of gridlines).

## Fix · The sheets with one way of gridlines: labels of a letter and a number · 2026-10-07

- **Summary:** after dashed gridlines were found, 60 of the 148 real sheets still had no grid, and I had put it down to their crossing lines being labelled on another sheet. They are labelled on the sheet itself: the main block's lines down the sheet are AC, AD, AE and its lines across are A4, A5 ... A12, each in a bubble like any other. A label had to be letters or a number, so "A4" was not a gridline's.
- **Labels (`drawings/grids.LABEL`, `kind`, `ordinal`):** a letter and a number is a gridline label too. Labels are of a kind (letters, a number, or a letter with a number, by its letter), the lines that run one way are of one kind, and A12 is twelfth among the lines lettered A. Rows of grid marks and families of lines are kept apart by kind, where they were by letters or numbers. A reference parts its two labels with a stroke unless one is letters and the other a number: "Grid AD/A10".
- **Two grids on a sheet (`GridSystem.local`):** with the main block's grid found, it has the more lines and the wing's was dropped. The grid with the most lines is the sheet's; the next pair of families square to each other, using neither of its families, is kept as `local`. A point among the local grid's lines is named by them, elsewhere by the main grid. Stored under `local_across` and `local_up`.
- **Grid bubbles where no row is clean (`_commonest_size`):** on two wing sheets the wing's bubbles stand along the top edge among the main block's, so the edge is one row in no order, no row was a grid's and the sheet was not looked at. There the grid bubble's size is the size at which the most labels appear once each. And a row's bubbles are now told by label and place, not label alone: a gridline S brought in every sprinkler.
- **Measured on the real bid:** 88 of 148 sheets with a grid before, 147 now; 67 with a second grid. At most 5.8 s a sheet. `views.DETECTOR_VERSION` is `4`.
- **Requirement IDs covered (test names):** FR-VIS-07: `tests/drawings/test_dashed_grid.py` (lines labelled with a letter and a number are gridlines; a label's kind and place in its sequence; a sheet with a main block and a wing keeps both grids; a wing's bubbles among the main block's along one edge are still found; a grid with a line S is found among sprinklers).
- **Not fixed:** no grid index from these grids (see the gap list); one sheet, A03-B2-05, whose lettered lines run three ways.

## Fix · Candidate symbols: a straight stroke and the screened base plan are not symbols · 2026-10-07

- **Summary:** a real sheet gave about 3,000 candidate symbols, 466,665 on the 148-sheet set, and four fifths of them were matched to a legend row. Two things were most of them. Two thirds were one straight stroke: every straight stroke has the same descriptor, and one legend row ("CONTROL VALVE", read on one sheet from a diagonal line beside it) had claimed 359,288 of them. Nine tenths were the architect's base plan, printed in light greys (0xBBBBBB to 0xEAEAEA) under the services in black, red and magenta: furniture, doors and fittings.
- **The rule (`drawings/symbols.candidates`, `screened`, `_straight`):** a cluster whose segment ends all lie on one line (no wider across than 2% of its length; a circle is never straight) is not a candidate. On a PDF, a cluster drawn in a screened grey (a grey, every channel from 0x80, not white) is not one. A CAD file is not printed, so its greys are layer colours and are kept; a block insert is always a candidate. Legends are read from the same candidates, so a legend row can no longer take a stroke or a piece of the base plan for its sample.
- **This is a decision made in the code.** The gap list had it down for the product owner and Senior Estimator. Nothing records what a sheet set aside; see the gap list.
- **Measured on the real bid:** 466,665 candidates before, 20,256 now (about 140 a sheet; at most 17.7 s a sheet to read). 4,850 match a legend row, 14,234 match none, in 799 groups. The 56 sheets with a legend now each give the same 16 rows (896); they gave 530 between them, some of them a title or a room label. Matching the bid takes 6 s where it took 41. `services/symbols.DETECTOR_VERSION` is `2`.
- **Requirement IDs covered (test names):** FR-VIS-02: `tests/drawings/test_symbols.py::TestWhatACandidateIs` (a straight stroke is no candidate; a circle is a candidate though it has no ends; the screened base plan of a PDF is no candidate; a CAD file's grey is a layer colour; a screened colour is a light grey).
- **Not done:** "Read again" does not read symbols again, so a bid read before this keeps its old candidates until its documents are read again from the start; the real bid's were re-read by a script. No test of the legend rows on a real sheet.

## Fix · The candidates no legend explains, reviewed: a leader is not a symbol · 2026-10-07

- **Summary:** after straight strokes and the screened base plan were set aside, 14,234 of the real bid's 20,256 candidates matched no legend row, in 799 groups. The 80 largest groups were drawn and looked at. In black they were nearly all leaders: an arrowhead and the strokes from its tip to a pipe size ("Ø150"), or a dimension's pair of arrowheads. In red they are pipe fittings, valve assemblies and pieces of devices.
- **The rule (`drawings/symbols._leaders`, `_arrowhead`):** a cluster of arrowheads and straight strokes and nothing else is not a candidate. An arrowhead is a triangle whose shortest side is at most 0.4 of its longest (a real one is 0.26; a check valve's triangle is near 1). Where there are strokes, one must start at an arrowhead's tip, so a slender triangle with a bar across its back is still a symbol.
- **Measured on the real bid:** 20,256 candidates before, 12,334 now, about 80 a sheet. Matched to a legend row: 4,850 before and after, row for row, so no match was lost. Matching no row: 6,312 in 613 groups. Matching the bid takes 4 s. `services/symbols.DETECTOR_VERSION` is `3`.
- **Found and not fixed (see the gap list):** a circled S, 528 on 59 sheets, which looks like a detector and matches no row because a filled box behind its letter is in its shape; devices drawn as several pieces that do not touch; fittings no legend shows.
- **Requirement IDs covered (test names):** FR-VIS-02: `tests/drawings/test_symbols.py::TestALeaderIsNoCandidate` (an arrowhead and the strokes from its tip to a note; an arrowhead alone and one drawn twice; a triangle that is not slender is a symbol; an arrowhead with anything else is a symbol; strokes that do not start at the tip are not its leader).
- **Not done:** the groups below the 80 largest were not looked at; what the pieces are was judged from drawings of one instance of each, in black, not checked with an estimator.

## Fix · The circled S: the filled box behind a letter is not part of a symbol · 2026-10-07

- **Summary:** 528 circles with an S in them, on 59 sheets of the real bid, matched no legend row. They are the legend's smoke detector exactly. On the plans the S is white on a filled red box, the box was taken into the symbol's shape, and a circle with a box in it is not a circle (0.307 from the legend's sample; 0.000 without the box).
- **The rule (`drawings/symbols._text_backings`, `_rectangle`):** a filled rectangle of a symbol's size, turned any way, with a text inside it (give or take 0.3 mm) and no more than four times that text's area, is the text's backing and is left out of every symbol's shape. Turned any way because the set's wing is skewed and its letters with it: square to the sheet only, 393 of the 528 were matched.
- **Measured on the real bid:** the group is gone; 536 circled S now match the smoke detector row at a distance of 0. Candidates 12,088 (12,334 before); matched to a legend row 5,140 (4,850). Matches to "REMOTE INTERCOM HANDSET" fell from 242 to 4: they were at 0.049, nearly out of tolerance, on shapes that had a letter's box in them, and were not looked at one by one. `services/symbols.DETECTOR_VERSION` is `4`.
- **Found and not fixed (see the gap list):** the smoke detector row also claims 1,803 circles of 4 mm with four ticks about them, which match its plain circle. Fixed in the next entry.
- **Requirement IDs covered (test names):** FR-VIS-02: `tests/drawings/test_symbols.py::TestTheBoxBehindALetter` (a symbol is the same with and without the box behind its letter; a filled box with no letter in it is part of the shape; a filled box much larger than the letter is part of the shape; the box is left out when the letter is turned with a skewed wing).

## Fix · The ticked circles: the letters in a symbol are part of what it is · 2026-10-08

- **Summary:** the real bid's smoke detector row (a circle with an S) claimed 1,803 circles with nothing in them, because a symbol was matched by its shape and the letter in it was not read. The same fault had every lettered box (FI, CM, SAP, FS, FM) matched to another box's row. A signature now carries the letters written inside the symbol, and two symbols with different letters are never the same symbol.
- **The rule (`drawings/symbols.Letters`, `Signature.letters`, `Candidates.gaps`):** a symbol's letters are the letters and digits of the texts wholly inside its box (give or take 0.3 mm), upper case, in alphabetical order, each text once: the same however the symbol is turned, whether "FS" is one text or two, or drawn twice. `""` is "nothing written in it"; `None` is "not read" (a signature stored earlier), which still matches by shape alone. Unexplained symbols are grouped by letters too, and the letters are in the group's key.
- **Stored proposals (`services/symbols._resolve`):** a proposed mapping stored without letters takes its legend row's signature, as a new version, when the legend is read again. A confirmed mapping is left as confirmed. `DETECTOR_VERSION` is `5`.
- **Measured on the real bid (12,088 candidates):** the smoke detector row claims the 536 circles with an S and nothing else. The 1,812 ticked circles are matched to "STROBE LIGHT (CEILING)", whose legend sample is the same drawing (a circle with four ticks): they were looked at side by side. CM 86 and FM 155 now match their own rows. Matched to a legend row 2,975 (5,140 before, 1,803 of them the ticked circles under the smoke detector row); to a proposal only 1,172; to nothing 7,941 in 626 groups (5,776 in 602). Matching the set again takes 3 s.
- **Not done, on purpose:** matching by size. The set draws the same device at other sizes on plan than in the legend (the manual call point at half, the mimic panel at 0.85), so size would lose true matches; the letters were what told the symbols apart.
- **Found and not fixed (see the gap list):** lettered boxes drawn to other proportions than their legend sample (FI, SAP, 2SFH, FS: about 630) now match no row; whether the ticked circle is the ceiling strobe needs the Senior Estimator; seven proposals no legend row uses still claim 1,172 symbols by shape alone.
- **Requirement IDs covered (test names):** FR-VIS-02: `tests/drawings/test_symbols.py::TestTheLettersInASymbol` (a signature carries what is written in the symbol; a note that runs across the symbol is not its letters; letters are the same written as one text or several or twice; the same shape with other letters is another symbol; a signature stored before letters were read matches by shape alone; the letters are kept when a signature is stored; candidates added one by one keep their letters), `tests/db/test_symbol_mapping.py::test_a_proposal_made_before_letters_were_read_takes_its_row_s_letters`.

## Fix · Open items, phase 1: stale proposals, symbols read again, one matching pass, CI once · 2026-10-08

- **Summary:** the first part of `docs/plan/OPEN_ITEMS_PLAN.md`: the fixes that need no decision and make each later measurement on a real tender cheaper.
- **A proposal claims symbols only while a legend row leads to it (`services/symbols._led_to`).** One left by an earlier reading of a legend is nobody's to confirm. A confirmed mapping needs no row (an "unlisted symbol" has none). On the real bid: 1,172 symbols matched to seven such proposals, 0 now; they are raised as unexplained (9,113 in 634 groups).
- **"Read again" reads symbols again (`parse_pipeline.symbols_again`, job `symbols.again`, parser pool).** A sheet's geometry record says which symbol detector read it (`sheet_geometry.symbols_version`, migration 0044); a sheet read before that was kept is this detector's if a symbol on it is. One job a bid reads each stale sheet from its stored geometry, a sheet at a time and each committed, then matches once and detects again what changed. `progress.read_again.symbols` counts them and the Documents page says so. Until now a detector change needed a script on the database.
- **A document that finishes matches only if a sheet was read (`services/symbols.match_if_read`).** `sheet_geometry.symbols_matched` is false from when a sheet's symbols are read until the bid is next matched; a pass clears only the sheets it saw when it began. A tender sent as 148 files matched the whole bid 148 times.
  - **Changed behaviour:** a mapping confirmed on another bid of the same consultant now reaches this bid's unexplained symbols when a sheet of this bid is next read, not when any document next finishes.
- **CI (`.github/workflows/ci.yml`):** `push` runs on main only, so a pull request is checked once, not twice. A pull request that changes only `docs/user/`, `docs/user-manual/` or `docs/presentation/` runs the security scans and no tests; nothing built or tested reads those folders.
- **Gap list:** closed "CI has not run since P2-06" (it passes on main), the scanners finding (the CI security job runs semgrep, gitleaks and trivy), and "four sheets were not read again" (148 of 148 are read, none with an error).
- **Not done:** the real bid's "Read again" was not exercised through the job (its sheets are all at this detector's version); a pass still reads every instance.
- **Requirement IDs covered (test names):** FR-VIS-02: `tests/db/test_symbol_mapping.py::test_a_proposal_no_legend_row_leads_to_claims_no_symbol`; `tests/db/test_parse_pipeline.py::TestReadAgain` (symbols read by an older detector are read again by one job; a sheet read before versions were kept is this detector's if a symbol on it is); `frontend/src/pages/Documents.test.tsx` (offers to read symbols again after the symbol detector changed). NFR-01: `tests/db/test_parse_pipeline.py::test_a_document_that_finishes_with_nothing_new_to_match_does_not_match_the_bid_again`.

## Fix · Open items, phase 2: lettered devices, what was set aside, pieces measured · 2026-10-08

- **Summary:** the second part of `docs/plan/OPEN_ITEMS_PLAN.md`, on symbol matching.
- **The same letters, two or more, loosen the shape (`drawings/symbols.LETTERED_TOLERANCE`, `Candidates.gaps`, `Match.how == "letters"`).** A consultant draws a lettered box to fit where it stands: the real legend's FI is 11 by 5.4 mm lying down, the plans' 3.5 by 6 standing up, 0.13 apart where the tolerance is 0.07. Two symbols with the same letters, two or more, are held to 0.2. One letter is not enough, and other letters still never match. On the real bid 172 FI, 103 SAP and 142 2SFH now match their rows (matched to a row: 3,395, from 2,975). About 200 FS are left, inside valve assemblies.
- **What the detector set aside is kept and said (`symbols.candidates(table, set_aside)`, `sheet_geometry.symbols_set_aside`, migration 0045, `GET symbols/counts` → `set_aside`, the Symbols page).** Counts by rule for each sheet, totalled for the bid's Current sheets. On the real bid: 494,875 shapes of the base plan, 49,579 straight strokes, 9,195 leaders, against 12,088 candidates. No switch turns a rule off for a bid: that waits for the rules to be confirmed.
- **"Read again" run on the real bid, as a person would ask it.** `DETECTOR_VERSION` is `6`, so all 148 sheets were offered. One `symbols.again` job read them in 17 min 45 s, none failed, matched once and queued 148 sheets for detection.
- **Pieces of a device, measured and not built (see the gap list).** 3 of the real legend's 16 rows are a piece of what is drawn in their cell. On plans there is no distance at which a device's pieces end and its neighbours begin, so joining by distance is ruled out.
- **Found:** a person naming an unexplained group in the workbench sees a count and no picture; with 578 groups that is not workable.
- **Not done:** existing bids are matched with the new tolerance only when read again; the Symbols page does not yet mark a match made by letters (its distance is stored).
- **Requirement IDs covered (test names):** FR-VIS-02: `tests/drawings/test_symbols.py::TestLettersOfTwoOrMore` (the same letters in a box of other proportions is the same symbol; a shape within the ordinary tolerance is matched by its shape; no letters and other letters are held to the ordinary tolerance; one letter is not enough; the nearest of two rows with the same letters is taken), `::test_what_was_set_aside_is_counted_by_rule`; `tests/db/test_symbol_mapping.py::test_what_each_sheet_set_aside_is_kept_and_totalled_for_the_bid`; `frontend/src/pages/Symbols.test.tsx` (says how many shapes were not read as symbols, and by which rule).

## Test · The first golden reference package: TC-SYN-001 · 2026-10-08

- **Summary:** the first package of `docs/plan/TEST_STRATEGY.md`: the expected work product of stages 1 to 7 (intake to takeoff) for the synthetic three-sheet sprinkler tender the workbench end-to-end test uses. In `eval/golden/synthetic/TC-SYN-001/`: `package.md` for people, `golden.json` for a comparison, `manifest.json` for where the inputs come from.
- **Where its values come from:** stages 5 and 6 (35 instances, pipe by size) are what the fixture generators say they drew, got by running the generators; the platform's readers were not called. Stages 1 to 4 and 7 were written by hand from the generators' source and `measurement_rules.yaml`.
- **Status: a draft, not verified by a person.** It was drafted in the session that built the platform, which the package says in its part 10.
- **Six ambiguities are recorded, not guessed away (part 11):** which heads take a drop (8.0 m against 12.0 m of DN25); whether the enlarged plan's scale is proved by a shared grid; the riser's size; an elbow at the riser; where the 5% pipe allowance is applied; and whether the main is measured as drawn or through its valves (8.05 m against up to 9.50 m of DN150, outside the 5% tolerance).
- **Guard (`tests/evals/test_golden_packages.py`, FR-LRN-01):** the package's counts, positions, pipe lengths and legend equal the generator's, and its takeoff counts the installation once. A change to the fixture that is not made to the package fails the build. The tests do not compare the platform with the package: that comparison is not built.

## Test · The second golden reference package: TC-SYN-002 · 2026-10-08

- **Summary:** the expected work product of all twelve stages for the synthetic basement car park tender: one plan with two general notes, the specification with its design and site-conditions section, the client's bill, the rate list and the productivity list. In `eval/golden/synthetic/TC-SYN-002/`. It ends at an estimate under review: SGD 6,378.11 before GST, 6,952.14 with it, six lines unpriced, G1 approved and G2 to G4 not decided.
- **The case was split.** The strategy planned TC-SYN-002 as the pump room tender through stages 1 to 12. No fixture bills, prices or specifies a pump: the specification, client BOQ, rates and productivity fixtures are all for the sprinkler installation. TC-SYN-002 is now that installation through stages 1 to 12, and the pump room tender is TC-SYN-003, stages 1 to 7, not yet written (`TEST_STRATEGY.md`, section 10).
- **It fixes a scenario**, because stages 8 to 12 depend on what people have decided: the legend and takeoff are verified and G1 approved; no specification attribute, rate proposal, labour multiplier, checklist item or risk is. The scenario is the bid `tests/db/test_submission.py` prepares, with the client's bill and the tender dates added.
- **Where its values come from:** stages 5 and 6 from running the generator; stage 8's attributes, obligations, issues, interfaces and risks and stage 9's mapping from the fixtures' own stated answers; stages 7 and 9 to 12 by hand-written arithmetic from the fixtures and `backend/config/`. No platform reader, takeoff, pricing, labour or risk function was called.
- **Status: a draft, not verified by a person.** To learn rules only the code states (rate matching, the hourly labour cost and its rounding, which build-up component a bill group feeds) the author read the platform's modules and some tests; the package says so in its part 10, and names stage 10 as where a person should look first.
- **Eleven ambiguities are recorded (part 11).** Those that move the total: which heads take a drop (6,378.11 against 6,454.39); the main as drawn or through its valves; whether an expired rate may price a line (376.20 of DN100); which pipe takes the 5% allowance (18.00); the client's 8,000 of provisional sums.
- **Guard (`tests/evals/test_golden_packages.py`, FR-LRN-01):** six tests for this package. Its counts, positions, pipe, notes and legend equal the generator's; its specification answers, risks, client bill, mapping, rates and productivity entries equal the fixtures'; every amount is quantity times rate and the build-up adds to its total; and the rules it records are still those in `backend/config/`. As before, nothing compares the platform with the package.

## Test · The third golden reference package: TC-SYN-003 · 2026-10-08

- **Summary:** the expected work product of stages 1 to 7 for the synthetic Phase 2 systems tender: the fire pump room, a typical floor, the site hydrant plan and the riser schematic. In `eval/golden/synthetic/TC-SYN-003/`. 17 pieces of equipment of 13 types, each once, with each pump's flow, head and power from the schedule; 62.85 m of drawn pipe over three systems.
- **Where its values come from:** stages 5 and 6 are what the generator says it draws, recorded as it places each symbol and pipe (`work_out.py`); the legend, schedule and levels are its constants. Stages 1 to 3 and 7 were written by hand from the generator's source and `measurement_rules.yaml`. No platform reader or takeoff function was called.
- **Status: a draft, not verified by a person.** The author had read `tests/db/test_systems_takeoff.py`, which asserts some of the platform's results for these sheets; the package says so in its part 10 and names what the tests do not state (the B1 rising main, the pump room's fittings, the hanger counts) as where a person should look first.
- **Seven ambiguities are recorded (part 11).** Two say the takeoff is short of the building the schematic shows: landing valves and hose reels on the four levels with no plan (1 and 2 counted, against 5 and at least 5), and the rising main's height (8.5 m by the rule, against at least 22.5 m by the level schedule).
- **Guard (`tests/evals/test_golden_packages.py`, FR-LRN-01):** four tests for this package. Its counts, tags and pipe equal the generator's; its legend, pump schedule and levels equal the consultant's; its takeoff counts each piece once; and the rules it records are still those configured.
- **Not done:** equipment at stages 8 to 12 has no synthetic case, because no fixture specifies, bills or prices it.

## Test · The platform compared with a golden reference package · 2026-10-08

- **Summary:** the comparison of `docs/plan/TEST_STRATEGY.md`, section 11, for stages 1 to 7. `firebid.evals.export_run` reads a bid's stage outputs from the database in the package's shape; `firebid.evals.golden` sets them against a package and gives differences by class, a score and a report. Commands: `firebid-eval export-run --bid <uuid> --out run.json` and `firebid-eval golden --case <TC_ID> --run run.json [--report x.md]`, which exits non-zero on a critical or a high defect.
- **How it compares:** each side becomes facts (the revision of a drawing, the count of a type on a sheet, the length at a size, an item's quantity). A fact the reference has and the run has not is missing; one that differs beyond its tolerance is wrong; one only the run has is listed for review and not scored. A difference on a value the package marks with an ambiguity is "to settle", and neither a defect nor a pass. The same item at another size is one difference of size. A stage that is not compared is reported as not compared, never as passed, and the score says what share of the weights it covers (85% so far: evidence and stage 12 are not measured).
- **Why the run is read from the database:** the in-memory pipeline the other suites use (`evals.qto_pipeline`) leaves out what the services add (it does not read the ceiling note, for one), so a comparison through it would not be of the platform.
- **First result, TC-SYN-001 (`tests/db/test_golden_run.py`):** 97.3% on what is measured; no critical or high defect; every count, every drawn length and all eight legend rows are the reference's. Two medium defects, both the platform's:
  - **the level of FP-L05-201 is read as "5 SPRINKLER LAYOUT PLAN"**, from its title "LEVEL 5 SPRINKLER LAYOUT PLAN", where the drawing number gives L05;
  - **the drawn reducer is held as DN150**, the size of the pipe it sits on: its outlet size (100) is not held.
- **To settle, six differences on two recorded ambiguities:** A1, the platform gives every head a drop (12.0 m against 8.0 m); A2, the platform leaves the enlarged plan's scale unverified and so does not measure that sheet (five facts).
- **The package was changed in two places, neither an expected quantity:** the enlarged plan's view kind is now the platform's own word, "enlarged plan", where the package had "plan"; and FP-L05-301's pipe lengths are marked as depending on A2, which they always did.
- **Not done:** the two defects are not fixed; TC-SYN-002 and TC-SYN-003 are not yet run against the platform; stages 8 to 12, evidence, semantic comparison and instance positions are not compared; a later difference is not linked to the earlier one that caused it.
- **Requirement IDs covered (test names):** FR-LRN-01: `tests/evals/test_golden_comparison.py` (every committed package is read and agrees with itself; a wrong count is critical at the stage it first shows; a pipe length is wrong only beyond 5%; a difference on an ambiguity is to settle and not scored; missing, extra and lesser facts; a stage not exported has not passed; the same item at another size; the `golden` command's exit codes), `tests/db/test_golden_run.py::test_the_platform_s_stages_1_to_7_against_tc_syn_001`, `::test_a_bid_with_nothing_read_exports_nothing`.

## Fix · A title that begins with LEVEL is not a level label · 2026-10-08

- **Found by:** the golden comparison on TC-SYN-001, as a medium defect at stage 2: the level of FP-L05-201 was stored as "5 SPRINKLER LAYOUT PLAN".
- **Cause:** the title block reader takes a run of text that begins with a label's word as the label with its value beside it (`REV: C1`). "LEVEL", "STOREY", "FLOOR", "ZONE" and "AREA" also begin titles, so "LEVEL 5 SPRINKLER LAYOUT PLAN" was read as a LEVEL label and the rest of the title as its value. The level in the drawing number was then never looked at, because a level had been found.
- **Fix (`drawings/title_block.py`):** what follows a level or zone label in the same run is taken only when it reads as one: a number or short code, a level's name, or a few of them ("5", "B1", "BASEMENT 1", "5 TO 7"). Otherwise the run is not a label, and the level comes from the drawing number as before. The same fault with "AREA OF ..." and the zone is fixed with it.
- **Effect:** stage 2 of TC-SYN-001 is now 21 of 21, and the package's score 98.0% on what is measured. One medium defect remains (the drawn reducer's outlet size), listed in `tests/db/test_golden_run.py`.
- **Not done:** a sheet already read keeps the level it was given until it is read again. A title that names a level where the drawing number has none ("LEVEL 5 PLAN" on drawing `FP-201`) now gives no level from the title block; the view's own title still gives the view its level.
- **Requirement IDs covered (test names):** FR-DOC-02: `tests/drawings/test_title_block.py::TestLayoutsTheGeneratorDoesNotDraw::test_a_title_that_begins_with_the_word_level_is_not_a_level_label`, `::test_a_title_that_begins_with_the_word_area_is_not_a_zone_label`, `::test_a_level_written_beside_its_label_in_one_span_is_still_read` (six wordings).

## Test · TC-SYN-002 and TC-SYN-003 run against the platform · 2026-10-08

- **Summary:** the other two synthetic packages are now compared with the platform on every build, stages 1 to 7, in `tests/db/test_golden_run.py`. Each case lists its known defects and its differences to settle exactly, as TC-SYN-001 does.
- **Results:**

  | Case | Score on what is measured | Defects | To settle | In the run and not in the reference |
  |---|---|---|---|---|
  | TC-SYN-001 | 98.0% | 1 medium | 6 (A1, A2) | 0 |
  | TC-SYN-002 | 98.0% | 1 medium | 1 (B1) | 0 |
  | TC-SYN-003 | 100.0% | 0 | 0 | 0 |

  - **TC-SYN-002** has the defect TC-SYN-001 has: the drawn reducer is held as DN150 and its outlet size (100) is lost. Its one difference to settle is B1, which is A1 again: the platform gives every head a drop (24 x 450 mm = 10.8 m); the package, the pendents only (7.2 m).
  - **TC-SYN-003** differs nowhere that is compared: 17 pieces of equipment once each, the pipe of three plans, and the derived risers, tees, elbows and hangers. C2 and C3 stay open: there the platform gives what the package's draft gives, which settles neither.
- **No new platform defect was found.** Every other difference on the first run was in how the two sides name the same thing, and was put right there:
  - *The package (TC-SYN-003):* the vertical pipe the riser rule gives was `"run": "rising main"`; it is `"run": "riser"`, the word the other two packages and the platform use. No quantity changed.
  - *The comparison (`evals/golden.py`):* a reference that does not say which drawn pipe is main and which is branch (TC-SYN-003 names the system instead) is compared by size alone; an equal tee is the same tee as `150` or `150x150`.
  - *The export (`evals/export_run.py`):* a specification or a workbook is no longer given a sheet count of 0.
- **What the comparison does not see, so what "100%" does not say:**
  - **Stages 8 to 12 of TC-SYN-002** (the specification's answers, the bill, the price, the risks, the review pack) are not compared, and are reported so.
  - **Stage 7 compares item, size, run and quantity.** It does not compare an item's level, its system or its attributes. On the pump room tender the platform puts the breeching inlet on L01 and the hydrants on a level named SITE, where the package gives no level; and names the pump room's pipe as the wet rising main's (ambiguity C6). The pumps' flow, head and power are not compared here: `tests/db/test_systems_takeoff.py` holds them.
  - **Document state at stage 1, for the specification and the client's bill, is the test's own.** The test calls the reading services directly; the parse job is what marks such a document done, and the fixture sets it as the job would. A drawing's state is the platform's.
- **Requirement IDs covered (test names):** FR-LRN-01: `tests/db/test_golden_run.py::test_the_platform_s_stages_1_to_7_against_tc_syn_002`, `::test_the_platform_s_stages_1_to_7_against_tc_syn_003`; `tests/evals/test_golden_comparison.py::test_drawn_pipe_is_compared_by_size_alone_where_the_reference_names_no_run`, `::test_an_equal_tee_is_the_same_tee_however_its_size_is_written`.

## Fix · A drawn reducer keeps both of its sizes · 2026-10-09

- **Found by:** the golden comparison on TC-SYN-001 and TC-SYN-002, as a medium defect at stage 7: the reducer drawn between the DN150 and the DN100 main was taken off as DN150.
- **Cause:** takeoff gave a valve or a drawn fitting one size, the largest of the runs ending at it. That is right for a valve; a reducer has two.
- **Fix (`qto/generate.py`):** a drawn fitting that is a reducer, with runs of exactly two sizes ending at it, keeps the smaller as `outlet_diameter_mm` (source: drawing) beside `nominal_diameter_mm`. Its description is `Fitting, DN150xDN100 (fitting reducer)`. `evals/export_run.py` gives its size as `150x100`. The bill's truth in `evals/synthetic_boq.py` and TC-SYN-002's record of the generator's description follow the new wording.
- **Effect:** stage 7 of TC-SYN-001 and of TC-SYN-002 has no defect. No known defect is open on any of the three synthetic packages at stages 1 to 7 (`KNOWN_DEFECTS` in `tests/db/test_golden_run.py` is empty); the differences to settle (A1, A2, B1) are unchanged.
- **Not done:**
  - **The bill and the price still read the larger size.** The company bill line is still "Reducer, 150 mm" (`config/boq_templates.yaml`) and the rate is still found at DN150. TC-SYN-002 expects both as they are; whether a reducer is billed and priced as 150 x 100 is an estimator's convention to settle, with the rate library.
  - **A bid taken off before this fix** gets a new reducer item at its next recompute (the attributes are part of the item's key): the old one is superseded, and a verification on it does not carry over.
  - **A reducer with one size, or more than two, at it** is as before: one size, the largest.
- **Requirement IDs covered (test names):** FR-QTO-05: `tests/qto/test_generation.py::TestCounts::test_valves_and_drawn_fittings_by_type_with_their_size`; FR-LRN-01: `tests/db/test_golden_run.py` (the three cases, with no known defect).

## Test · The comparison taken to stages 8 to 12 · 2026-10-09

- **Summary:** the export and the comparison now cover all twelve stages, and TC-SYN-002 is run through them on every build: the specification as read, the company bill and the client's against it, the price and the labour, the clarification candidates, checklist and risks, and the review pack's figures and gates.
- **Built:**
  - `evals/export_commercial.py`: stages 8 to 12 of a bid in the package's shape, called by `evals/export_run.py`. It reads what is stored, and asks for the build-up, the labour estimate and the review pack as the platform's own pages do.
  - `evals/golden.py`: the facts of stages 8 to 12; a tolerance of an amount (a cent on a labour cost) beside the tolerance of a percentage; and the **other reading** of an ambiguity (below).
  - A bill line is named by what it is of (`heads_pendent`, `pipe_150_main`, `tee_150x50`), from the takeoff items behind it, so wording and numbering do not matter. TC-SYN-002's reducer line is now `reducer_150x100`.
- **The other reading of an ambiguity.** B1 (does every head take a drop?) changes every money figure after the takeoff. Marked only as ambiguous, all of them would be "to settle", and a wrong price would hide there. TC-SYN-002 now records, stage by stage, what the other reading gives (`alternatives`, worked out by `work_out.py` as the rest is). A run that gives that value is to settle; one that gives neither value has a defect.
- **Result, TC-SYN-002 (`tests/db/test_golden_run.py`), 377 scored checks:** 98.8% on the 90% of the weights measured. Stages 1 to 9 pass on every scored check.
  - **The price agrees to the cent.** On the other reading of B1 the platform gives exactly what the package worked out by hand: SGD 6,454.39 before GST, 7,035.29 with it; 52.21 man-hours, SGD 1,035.11 of labour; every line's rate, amount, warnings and hours. 26 differences are to settle with B1 and all 26 are the other reading. The package's own figure (6,378.11) is what the pendents-only reading gives.
  - **Three defects by class, none shown to be the platform's:**
    1. *Critical, the test's own:* the tee whose only rate names a brand is "proposed" in the package and "unpriced" in the run. The platform leaves that proposal to the model, and the test runs none.
    2. *High, for a person:* open checklist items, 9 against 3. The platform builds the checklist for the hose reel and hydrant systems the specification has sections for; the package, for the sprinkler system that is drawn.
    3. *High, for a person:* flagged bill variances, 7 against 2. The platform flags the five lines the client's bill has no item for; the package counts the client's two lines.
  - **To settle with B10:** the basement factor. The platform applies it to the bill lines that carry level B1, which are the three lines of heads (10.40 h, SGD 20.88); the package applies it to all the labour (SGD 101.02). The pipe, fitting and valve lines of the bill carry no level, though all of it is on B1: likely the platform's to fix, and not decided here.
  - **In the run and not in the package (33):** two obligations section 8 repeats from section 6; the hydrant excavation row; a clarification candidate for each of the five lines the client's bill lacks and each of the seven unclear scope rows; and the 18 checklist rows of the two systems not drawn. Each is listed in the test.
- **Not done:**
  - **Wording is not compared** (semantic, type B): an issue's title, a clarification's text, a qualification's text. Nor is evidence (type D): which clause or sheet a finding cites, beyond a clause number in its key.
  - **At stage 8 an attribute's or obligation's state, and at stage 10 a rate's source and validity date, are exported and not compared.** Labour rates by grade are not exported.
  - **Only TC-SYN-002 has stages 8 to 12.** Equipment has no case there (TC-SYN-003 stops at stage 7).
  - **The scenario is the test's.** People's steps (verifying the takeoff, G1, the margin) are done by the fixture as the scenario states them, not through the API.
- **Requirement IDs covered (test names):** FR-LRN-01: `tests/db/test_golden_run.py::test_the_platform_s_twelve_stages_against_tc_syn_002`; `tests/evals/test_golden_comparison.py::TestTheLaterStages` (a wrong total is critical where it is made; the other reading is to settle and a third value a defect; a labour cost a cent out; a bill line known by what it is of; a missing issue or risk; an alternative for a value the package does not have is refused), `::test_every_committed_package_is_read_and_agrees_with_itself`.

## Fix · A level's labour multiplier reaches lines billed for the building · 2026-10-09

- **Found by:** the golden comparison on TC-SYN-002 at stage 11 (ambiguity B10): the basement factor's impact was SGD 20.88, on the three lines of heads, where all the labour of the bid is on basement 1.
- **Cause:** the company bill keeps sprinkler heads by level and rolls pipework, fittings, valves and hangers up over the building, so those lines carry no level. A multiplier for a level (a basement, a ceiling height) was applied to a line by the line's level, and so never reached them: neither in a risk's computed impact nor, once a person confirmed it, in the labour estimate itself.
- **Fix:** a line rolled up over the building is worked level by level, from the takeoff items behind it (`labour/estimate.py`: `Portion`; `services/labour.py`). Each level's quantity takes that level's multipliers; the line's hours are the sum, and its factor what they come to over the baseline. A risk on a level reaches the part of each line that is on that level (`services/risk.py`). A line whose takeoff items do not account for all of its quantity is left whole, as before.
- **This changes a rule that was written down.** `tests/db/test_labour.py` held that "pipework is billed for the building: the bid's multiplier, not a level's". It now holds that pipework on a level carries that level's multiplier. A bid with a confirmed level multiplier will show more labour hours on its pipe, fitting and valve lines the next time its estimate is opened; a figure an estimator entered for labour is not touched.
- **Effect on TC-SYN-002:** the basement's impact is 5.22 man-hours, SGD 103.51: what the package's reading of B10 gives on the other reading of B1 (5.10 and 101.02 on its own). The package records that value, and the two differences are now with B1's other reading, 28 in all. B10's row in the package still names the product owner: this fix follows the package's draft, and is theirs to confirm.
- **Not done:** the portions are not in the API or on the labour page, which show the line's blended factor (for example 1.2667) and the multipliers it carries anywhere. A line with a level of its own is as before.
- **Requirement IDs covered (test names):** FR-LAB-02: `tests/labour/test_labour.py::TestHours::test_a_line_rolled_up_over_the_building_is_worked_level_by_level`, `::test_a_line_all_on_one_level_comes_to_what_that_level_s_line_would`, `tests/db/test_labour.py::TestEstimate::test_hours_match_a_hand_calculation_with_every_factor_shown`; FR-RSK-04: `tests/db/test_risk.py::TestExecution::test_a_level_s_risk_reaches_the_labour_on_that_level_in_lines_billed_for_the_building`.

## Test · An item's level compared at the takeoff · 2026-10-09

- **Summary:** stage 7 of the golden comparison now compares the level of each kind of item, where the package states levels: once for the whole takeoff (TC-SYN-002) or item by item (TC-SYN-003). An item the package gives no level is expected to have none. A difference is high: since the labour fix above, a level decides which labour a level's multiplier reaches.
- **TC-SYN-001 now states its level** (`"level": "L05"` at stage 7), as TC-SYN-002 states B1. Its three sheets are of level 5; no quantity changed.
- **Results:** TC-SYN-001 and TC-SYN-002 pass on every level. **TC-SYN-003 has four differences**, all where the package gives no level and records no ambiguity, so they are classed as defects (high) and are for a person to place:
  - *The site plan.* The platform puts the hydrants, the hydrant main (26.25 m of DN150) and its three tees on a level it calls SITE. The sheet itself is registered with no level at stage 2, and the package gives the items none. Either the platform should not name a level the register does not have, or the package should expect an external level.
  - *The breeching inlet.* Only the schematic draws it. The platform puts it on L01, the level the schematic names nearest it; the package gives it none, "it is on no plan". The platform's reading is the more useful one, and the package may be the side to change.
- **No count, length or derived quantity changed**: TC-SYN-003's stage 7 is 52 of 56, the four being levels.
- **Not done:** a kind of item on two levels is compared by the set of levels, not by the quantity on each. Systems and attributes at stage 7 are still not compared.
- **Requirement IDs covered (test names):** FR-LRN-01: `tests/evals/test_golden_comparison.py::TestLevelsAtTheTakeoff` (an item on another level; an item the reference gives no level; a level stated once for the takeoff; a missing item reported once), `tests/db/test_golden_run.py` (the three cases).
