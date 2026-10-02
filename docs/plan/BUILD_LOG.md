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
