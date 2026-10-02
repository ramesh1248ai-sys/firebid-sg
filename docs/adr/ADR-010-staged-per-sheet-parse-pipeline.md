# ADR-010: A staged, per-sheet parse pipeline

- **Status:** Proposed; built and measured in P1-11 (29 Sep 2026)
- **Date:** 2026-09-29
- **Deciders:** Tech Lead
- **Requirements:** NFR-01 (300 sheets ingested and classified within 1 hour), NFR-02 (scale); guardrail 9 (parsers run only in the sandbox pool)
- **Amends:** ADR-006, which says how a job is queued and run; it said nothing on how large one may be.

## Context

A drawing set was read by one `parse.document` job, in one database transaction. That job covered sheets, tiles, title blocks, geometry, views, symbols, detection and classification, for every sheet in turn.

A real tender of 121 A1 sheets (P1-11 benchmark) took **64 minutes**. At that rate, 300 sheets take about 2.6 hours against the 1-hour target. The measurement showed five problems with the shape of the job:

- **No parallelism for one tender.** A tender is one job on one process in one pod. Adding sandbox pods speeds up many tenders, never one.
- **All or nothing.** 64 minutes of work commit at the end.
  - A crash at sheet 120 loses all of it, and the retry starts from zero.
  - Nobody can see progress: every row has the transaction's start time, and the document looks untouched until the end.
- **Memory grows with the tender.** The session keeps every object it loaded until commit, including 163,000 symbol instances for this tender. The host ran out of memory during the run.
- **Long transactions.** An hour-long transaction holds locks, and blocks Postgres's clean-up of old row versions across the database.
- **Lost work is never retried.** A job whose worker dies stays `doing`, and its document stays `processing`, for ever.

## Decision

Split the job into three kinds of small job, each its own transaction, all on the `parse` queue, which only the sandbox pool takes (guardrail 9 unchanged):

```
parse.document   check the scan, list the pages, register the sheets; queue one parse.sheet each
  └─► parse.sheet × N   one sheet, start to finish:
  │        heavy, no lock:       low tiles, geometry, title block reading and crop, symbol shapes
  │        bid lock, commit:     the title block proposal
  │        no lock:              views, symbol instances (a legend sheet takes the lock for its rows)
  │        bid lock, commit:     "sheet parsed", and, for the last sheet, queue parse.finish
  └─► parse.finish      bid-wide: match symbols, detect, classify, queue the takeoff; document `done`
```

**What changes for callers:**

- **Document states keep their meaning.**
  - `processing` now lasts until `parse.finish` has run. `done` still means "read, classified and detected", which is what it meant when the single transaction made it visible.
  - `Sheet.parsed_at` records each sheet as it finishes, and `parse_error` records why it failed.
  - Ingestion progress reports sheets parsed out of sheets found.
- **Idempotency is per sheet.**
  - `parse.document` queues only sheets not yet parsed. When they are all parsed, it queues `parse.finish` instead.
  - `parse.sheet` does nothing for a sheet already parsed.
  - Every stage inside it was already idempotent per sheet (stage cache, "replace this sheet's rows").
  - `parse.finish` is idempotent across the bid, and carries a queueing lock per document, so a duplicate waits rather than running twice at once.
- **Failure is a state, per sheet.** A sheet whose job raises is still marked parsed, with its error, in a new transaction. So one bad sheet never holds up the other 299, and the register shows the failure. A document whose finish fails becomes `rejected`, with the reason.
- **Cross-sheet bookkeeping is serialised per bid.** Some steps read or write state shared between sheets:
  - a title block proposal: renditions of the same revision, and the current revision of a drawing number;
  - legend rows proposing organisation mappings;
  - counting the sheets left.

  Each runs under `pg_advisory_xact_lock` on the bid.

  - **Before the lock:** the expensive work (tiles, geometry, reading the title block and rendering its crop, finding symbol shapes).
  - **Under the lock:** the title block proposal. The sheet job commits straight after it, releasing the lock.
  - **No lock:** views and symbol instances, which are the sheet's own rows. A legend sheet takes the lock again for its legend rows.
  - **Under the lock, briefly:** the final "parsed" mark and count, held only until the commit.

  On six real sheets, all the locked work together took 0.2 s, and no sheet waited for the lock (P1-11 benchmark).
- **The last sheet queues the finish, exactly once in effect.** Sheets count "left to parse" under the bid lock, so exactly one sees zero. A duplicate finish is harmless: it is idempotent and queue-locked.
- **Lost jobs are retried.** A periodic `system.retry_stalled_parse` job re-queues `parse` jobs whose worker has stopped sending heartbeats. Because every parse job is idempotent, a retry is always safe.

## Consequences

- **Scaling:** one tender's time is roughly its total sheet work ÷ (sandbox pods × `parse_concurrency`), plus the per-bid serial floor and the finish. Each sandbox pod takes two sheet jobs at once (`FIREBID_WORKER_CONCURRENCY=2`), and the pool autoscales on CPU to 4 pods (ADR-008), so a 300-sheet tender runs 8 sheets at a time. Scaling on parse-queue depth would react sooner; it needs an external metric, and is left until measurements ask for it.
- **Memory:** each job holds one sheet's objects, not a tender's.
- **Progress and timings are real:** rows commit as sheets finish, so their timestamps, and the progress bar, show what happened when.
- **One BLAS thread per process.** The images set `OPENBLAS_NUM_THREADS=1` and `OMP_NUM_THREADS=1`. numpy sizes its thread pool from the host's CPUs, not the container's limit, and idle BLAS threads spin. With two sheet jobs on a 2-CPU sandbox, finding symbols ran 40 times slower until this was set. Parallelism comes from processes, one per sheet job.
- **More, smaller jobs:** 300 sheets are 302 jobs. Procrastinate's table and event history grow with them. The retention job prunes finished job history after 90 days (`backend/config/retention.yaml`).
- **Each sheet job fetches the tender file** from object storage and hands it to its sandboxed calls. For large PDFs this is the next cost to remove: split the PDF into one file per page once, in `parse.document` (proposal B of the P1-11 analysis).
- **Detection stays in `parse.finish`,** serially, because it needs every sheet's symbols matched first. After P1-11 it takes about a second a sheet. If it grows, it can fan out the same way.
- **Tests** that ran `parse.document` and expected everything read must now run the queued sheet and finish jobs too. The tests use a helper that runs a document's jobs to completion, as a worker would.
