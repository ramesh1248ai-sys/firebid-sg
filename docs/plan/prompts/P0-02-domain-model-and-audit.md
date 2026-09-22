# P0-02 · Domain Model, State Machines and Audit Log

**Builds on:** P0-01 (repository, local stack, traceability tooling). **Needs:** nothing.

Begin by replying with a plan that maps each item under **Done when** to the work that satisfies it. Wait for approval before changing files.

## Read first

- `docs/plan/project-context.md`: conventions (units, money, IDs, immutability, audit, state machines).
- Requirements: §7 (state models), §11.1–§11.2 (data stores, entities), Appendix B (evidence record), NFR-07, NFR-09.
- `docs/plan/BUILD_LOG.md`.

## Goal

Create the persistence and domain core for Phases 0–1. That means the entities, the three state machines Phase 1 needs, the evidence-record and lineage types, and a tamper-evident, append-only audit log. Later steps add their own entities when they need them.

## Scope

FR-DOC-07 (lineage fields), FR-ADM-04 (audit query and export API), NFR-07 (personal-data inventory), NFR-09 (immutable audit log).

## Build

1. **Value types** in `domain/`:
   - `LengthMm` (int)
   - `Money` (Decimal SGD, half-up rounding)
   - `Confidence` (0–1)
   - `SourceRef` (document, sheet, revision, page or layout, region bbox in sheet mm): this is the lineage for FR-DOC-07
   - `EvidenceRecord` (all Appendix B fields)
   - `ExtractionMethod` and `CalculationMethod` enums
2. **Entities and Alembic migrations** for:
   - Organisation, User, Project, Bid, BidMember
   - TenderPackage, Document, Sheet, SheetRevision, Addendum
   - DetectedObject, QTOItem, Evidence, MeasurementRule
   - BOQ, BOQLine, ClientBOQ, ClientBOQLine, ClientBOQMapping
   - Rate, Approval, HumanTask, AgentRun, AuditEvent

   Use UUID keys, human IDs per bid (`BID-YYYY-NNN`, `QTO-NNNNNN`) from a concurrency-safe generator, `created_by`/`created_at`, and versioning (`version`, `supersedes_id`) on records that become verified or approved.

   Use SQLAlchemy 2.0 with synchronous sessions on psycopg 3, with one session per request or job. Every bid-scoped table carries `bid_id`, which P0-03's row-level security relies on.

   Partition now, before the tables hold data: hash-partition `detected_object`, `qto_item` and `evidence` by `bid_id`, and range-partition `audit_event` by month. Add a job that creates future monthly partitions ahead of time.
3. **State machines** as explicit transition tables with guards (required role, preconditions) for:
   - bid lifecycle
   - document/sheet revision
   - QTO item

   Take them from requirements §7. Services change state only through them. An illegal transition raises a domain error. Each successful transition writes exactly one audit event in the same database transaction.
4. **Audit log.** `audit_event` is append-only. Enforce it in the database: revoke UPDATE/DELETE from the app role and add a trigger that rejects them.
   - Each event stores actor, action, entity, before/after (JSON diff), reason and transaction ID.
   - Tamper evidence is a hash chain **per bid** (organisation-level events use their own chain). Each database transaction appends one link that hashes all of that transaction's events for the bid together with the bid's previous link.
   - Take a per-bid advisory lock only while appending the link, so writes to different bids never wait on each other. A bulk action of thousands of items costs one link, not thousands.
   - Provide `verify_chain(bid_id)`.
5. **Audit API** (FR-ADM-04 backend): query by bid, entity, actor, action and time range; paginated; CSV export.
6. **Personal-data inventory** (NFR-07): mark columns holding personal data in model metadata, and generate `docs/data-inventory.md` from that metadata with a script.
7. **Object storage abstraction** (`storage/`): put, get and presigned URLs, with originals written once (no overwrite of an existing key).
8. **Transactional job queuing:** a `jobs.enqueue()` helper that queues a Procrastinate job inside the caller's database transaction, so a rollback leaves no job behind. Add a job-ID and idempotency-key convention for later steps.
9. **Test infrastructure:** use Testcontainers for PostgreSQL, so each test session gets an isolated database with migrations applied. Add Hypothesis property tests for `LengthMm` and `Money` (conversions, rounding, arithmetic invariants).

## Done when

- `alembic upgrade head` and `alembic downgrade base` both succeed on an empty database, and the test suite runs against a migrated database.
- For each of the three state machines, a table-driven test passes every legal transition, gets a domain error for every illegal transition, and gets exactly one audit event per successful transition.
- A test shows the database rejecting UPDATE and DELETE on `audit_event`, and `verify_chain(bid_id)` detects a row altered with superuser rights. Tagged NFR-09.
- A concurrency test writes audit events for two bids in parallel transactions, and neither waits on the other's chain lock. A bulk transition of 5,000 items appends exactly one chain link. Tagged NFR-09.
- A test shows a job queued inside a transaction that then rolls back never runs, while one in a committed transaction does.
- Partitioned tables accept inserts across several bids and months, and the future-partition job creates next month's partition.
- Hypothesis property tests pass for `LengthMm` and `Money`.
- A test shows `SourceRef` round-trips through persistence with every lineage field. Tagged FR-DOC-07.
- Audit API tests cover each filter and the CSV export. Tagged FR-ADM-04.
- `docs/data-inventory.md` is generated from model metadata. Tagged NFR-07.
- A build log entry is appended.
