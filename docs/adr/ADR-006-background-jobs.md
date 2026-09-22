# ADR-006: Background jobs

- **Status:** Proposed; evidence from step P0-01 below
- **Date:** 2026-09-22
- **Deciders:** Tech Lead
- **Requirements:** §12.1; NFR-01, NFR-02, NFR-04; project-context convention "Jobs"

## Context

Ingestion, rendering, geometry, takeoff and agent work run in the background. A job must never be lost between saving data and queuing its work, or run against data that was rolled back. Delivery must survive restarts, and the team is small, so every extra system costs operations and backup effort.

## Options

1. **PostgreSQL-backed queue (Procrastinate).** Jobs are rows in our database, so a job can be queued in the same transaction as its data. Retries, periodic jobs and job history live in SQL. There is nothing extra to run or back up.
2. **Celery with Redis or RabbitMQ.** Very widely used, but it needs a broker plus an outbox pattern for transactional safety. Redis-based delivery can re-run long tasks after the visibility timeout, and there is one more system to operate.
3. **Temporal.** Excellent durable workflows, but a cluster to run and a heavy programming model for simple jobs. It stays a candidate for the Phase 4 bid orchestration (ADR-007, ADR-009).

## Recommendation

**Option 1: Procrastinate on PostgreSQL.** Details:
- `firebid.jobs.enqueue(session, task, **kwargs)` queues on the SQLAlchemy session's own connection.
- Each worker process runs one synchronous job at a time. Throughput scales by adding worker processes (containers).
- Alembic owns the queue's schema. When Procrastinate is upgraded, add a revision applying the SQL files it ships in `procrastinate/sql/migrations`.
- Delivery is at-least-once, so every task is idempotent (e.g. results are written with `ON CONFLICT DO NOTHING`).
- A heartbeat job runs every minute. `/health` and the worker container health check report the queue unhealthy when it is stale.

**Evidence (P0-01, against PostgreSQL 17).** Procrastinate 3.9 supports queuing on a caller-supplied connection (`task.configure(connection=...)`), so no outbox table is needed. Integration tests confirm:
- a job queued in a rolled-back transaction never exists;
- a job queued in a committed transaction is visible;
- the worker runs a job queued through the API and records its result;
- the heartbeat keeps `/health` green.

Migration `0001` upgrades, fully downgrades (no leftover objects) and re-upgrades.

## Consequences

- One database to operate and back up. Job state is queryable in SQL for dashboards and support.
- Queue load shares the database. Watch queue depth and table bloat (monitoring in P1-11), and revisit if job volume reaches thousands per second, far beyond the expected load.
- No Redis. Shared counters such as rate limits also live in PostgreSQL, unless measurements justify Redis through a new ADR.
