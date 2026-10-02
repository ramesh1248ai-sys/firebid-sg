# Runbook: restore the database or documents

**Targets (NFR-04):**

- **Data lost (RPO):** at most 24 hours. Point-in-time recovery keeps it to minutes.
- **Time to restore (RTO):** at most 8 hours.
- **Submission snapshots:** immutable for the retention period.

**Who:** the platform engineer on duty. The incident lead decides the recovery point.

## What is kept, and where

| Data | Protection | Kept |
|---|---|---|
| Database (Cloud SQL, PostgreSQL 17) | daily backup at 02:00 SGT; transaction log for point-in-time recovery; regional HA in production | backups 30 days, log 7 days |
| Documents bucket | object versioning, CMEK | old versions per the retention policy (`config/retention.yaml`) |
| Snapshots bucket | retention policy under bucket lock (production): nothing can be deleted or overwritten | `snapshot_retention_days` |
| Audit log | in the database, hash-chained per bid; monthly partitions archived to the documents bucket | ≥ 7 years |

## Decide first

1. **What was lost, and when?** Find the last good moment. Sources:
   - the incident timeline;
   - the audit log (`/audit`);
   - Cloud Logging.

   The recovery point is just before the damage. When unsure, go earlier: work done after the point can be redone from the audit log.
2. **Whole database, or one bid?**
   - For one bid's mistake (a deletion, a bad import), restore to a clone and copy that bid's rows back. Never roll the whole database back for one bid.
   - For corruption or loss of the whole database, restore the whole database.
3. **Stop the writes.** Scale the workers to zero, so jobs cannot keep changing the damaged database:

   ```sh
   kubectl -n firebid scale deployment firebid-worker firebid-sandbox --replicas=0
   ```

   Post a banner, or tell the estimators, that the platform is read-only.

## Restore the database to a point in time

```sh
gcloud sql instances clone firebid-$ENV firebid-$ENV-restore-$(date +%Y%m%d%H%M) \
  --project $PROJECT --point-in-time 2026-10-02T09:40:00Z
```

The clone is a new instance with the same settings; the original is untouched. Time the clone, because it is the restore part of the RTO.

**Verify the clone** before anything points at it. Use Cloud SQL Auth Proxy to both instances, as the owner role:

```sh
firebid-ops --root eval restore-drill --environment $ENV \
  --original postgresql+psycopg://postgres:...@127.0.0.1:5433/firebid \
  --restored postgresql+psycopg://postgres:...@127.0.0.1:5434/firebid \
  --restore-minutes <clone minutes> --data-lost-minutes <minutes back from the damage>
```

This checks two things:

- every table's rows, clone against original. After real damage the damaged tables will differ, and the drill says which ones: read that list, it should name only what you expected;
- every audit hash chain in the clone, which must be intact.

Its evidence goes to `eval/results/ops/restore-drill.json`.

**Whole database:** point the application at the clone.

1. Rename the instances: keep the damaged one as `-damaged`, and promote the clone to the name Terraform expects. Alternatively, update `database_private_ip` in the secrets.
2. Rerun `infra/k8s/sync-secrets.sh`.
3. Restart the deployments: `kubectl -n firebid rollout restart deployment`.
4. Scale the workers back up.

**One bid:** copy that bid's rows back from the clone, table by table, in a transaction, as the owner role. Rows carry `bid_id`, so filter on it. Afterwards, verify that bid's audit chain: open the bid's audit page, or run `verify_chain`. Record the restore as an audit event with the reason.

Delete the clone and the damaged instance only after the incident review.

## Restore documents

- **A deleted or overwritten object:** restore its previous version.

  ```sh
  gcloud storage ls --all-versions gs://<documents bucket>/<key>
  gcloud storage cp gs://<documents bucket>/<key>#<generation> gs://<documents bucket>/<key>
  ```
- **Submission snapshots** cannot be deleted or changed while the bucket is locked, so there is nothing to restore. Nothing writes to this bucket yet: submission packages arrive in a later phase. That phase's snapshot table will be the index to check when one seems missing.

## After

- Scale the workers back up (`--replicas` as in the overlay), and confirm `/health`.
- Record, in the incident log and in `eval/results/ops/`:
  - the recovery point;
  - the clone time;
  - the verification result;
  - the data lost.
- Rehearse this runbook in staging every quarter, and after any change to backups. The last local rehearsal restored 120 MB and verified 58 tables and 83 audit chains in 0.2 min (`firebid-ops restore-drill --local`). The staging rehearsal for the Phase 1 gate is still to run.
