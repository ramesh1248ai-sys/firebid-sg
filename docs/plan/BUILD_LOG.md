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
