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
