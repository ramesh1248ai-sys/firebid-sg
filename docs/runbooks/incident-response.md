# Runbook: incident response

**Scope:** anything that stops estimators working, loses or exposes tender data, or breaks the audit trail.
**Service hours:** Singapore business hours, the NFR-03 availability window (99.5%).
**Deadline rule:** a bid due within 48 hours outranks everything else. Ask first which bids are due, using `firebid-ops deploy-guard` for the next hours or the bid list.

## Severity

| Severity | Examples | Response |
|---|---|---|
| **S1** | platform down; a bid due within 48 h cannot proceed; tender data exposed to someone not on the bid; audit chain broken | immediately, all hands; the product owner is told within 30 min |
| **S2** | one capability down (parsing, model calls, exports) with a workaround; parse queue backing up | within 1 h |
| **S3** | degraded, cosmetic, or one user | next working day |

Any suspected **data exposure** is S1 until shown otherwise. It also follows the company's PDPA and personal data breach procedure: notification decisions are the data protection officer's, not the engineer's.

## First 15 minutes

1. **Take the lead, or name who has it.** One incident lead decides; everyone else reports to them.
2. **Open the incident log:** a dated note, with times in SGT. Record what you see, what you do and when. The review is written from it.
3. **Check the scope:**
   - `https://$HOSTNAME/health` shows each dependency: database, job queue heartbeat, malware scanner, parser sandbox and LLM routing;
   - Cloud Monitoring: which alert fired, and since when;
   - Cloud Logging: filter `jsonPayload.level="error"` over the last hour;
   - the parse queue depth (`queue_depth` log events).
4. **Protect data first, availability second.** For suspected exposure, stop the leak before anything else:
   - disable the account;
   - revoke the key ([key-rotation.md](key-rotation.md));
   - or scale the affected workload to zero.

## By alert

| Alert | Likely cause | First actions |
|---|---|---|
| **unavailable** (uptime check) | API pods failing, load balancer, database | `kubectl -n firebid get pods`; `kubectl -n firebid logs deploy/firebid-api --since=15m`; Cloud SQL status. A failed release: roll back ([deploy.md](deploy.md)). |
| **5xx rate** | a code fault, the database under strain | Logs for the failing route; recent release? Roll back. Database alert too? See "database under strain". |
| **job queue backing up** | sandbox pool scaled down or crashing; one huge document | `kubectl -n firebid get pods -l tier=sandbox`; the sandbox logs for the current `parse.document` job. A tender of several hundred sheets takes tens of minutes and is not a fault. A job that fails repeatedly: see `parse_failed` events. |
| **parse failed** | an unreadable or hostile file | The document is marked failed and the estimator sees why. Nothing to restore. If the file is hostile (the sandbox reported a resource limit or a crash), keep it quarantined and tell security. |
| **database under strain** | a runaway query; storage filling | Cloud SQL Query Insights; cancel the query (`pg_cancel_backend`); storage above 85%: raise the disk size (online) and find what grew. |
| **audit partition failed** | the nightly job could not create a partition | Partitions are made three months ahead, so there is time: S2. Run it by hand: `kubectl -n firebid exec deploy/firebid-worker -- python -c "from firebid.jobs.tasks import ensure_audit_partitions; print(ensure_audit_partitions(0))"`. If the current month's partition is missing, audited writes fail: S1. |
| **audit chain broken** | tampering, or a restore that missed rows | S1. Do not "fix" the chain. Preserve the database (take a clone), find the first broken link (`verify_chain` names it), and involve security. The nightly check (`system.verify_audit_chains`) records which bids. |

**LLM provider outage.** No alert is needed. The gateway falls back to each route's approved alternative. A route with no approved fallback escalates to the estimator, who carries on by hand; nothing is lost. The last rehearsal (`firebid-ops game-day`, local): 9 routes fell back, 1 escalated, 0 failed. Watch escalations on the KPI page. If the outage lasts, tell estimators which steps are manual for now.

## Communicate

- **S1:** the product owner within 30 minutes, then updates every hour until resolved.
- Estimators working on a bid due within 48 hours hear directly. Tell them what works, what does not, and when you will update them next.
- Say what is known. Do not guess at causes in messages to users.

## Close

- Confirm recovery: `/health` is OK, alerts are clear, and an estimator has confirmed their work continues.
- Hold the incident review within five working days (S1 and S2):
  - the timeline from the log;
  - the cause;
  - what detected it, and how long detection took;
  - actions, each with an owner and date.

  The review blames no one.
- If data was restored, attach the restore evidence ([restore.md](restore.md)).
