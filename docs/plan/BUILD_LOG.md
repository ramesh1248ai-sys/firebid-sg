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
