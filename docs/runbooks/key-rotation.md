# Runbook: rotate keys and passwords

**How secrets flow:**

1. Every production secret lives in Secret Manager as `firebid-<env>-<name>` (Terraform `secret_names`).
2. `infra/k8s/sync-secrets.sh` builds the cluster's Kubernetes secrets from Secret Manager.
3. The pods read those secrets at start-up.

So every rotation takes the same four steps:

1. **Add** the new value as a new Secret Manager version. Old versions stay until you disable them.
2. **Sync:** run `infra/k8s/sync-secrets.sh $PROJECT $ENV $DATABASE_IP`.
3. **Restart** the deployments that read the secret, then check `/health`:

   ```sh
   kubectl -n firebid rollout restart deployment/<name>
   ```
4. **Retire** the old credential at its source, then disable the old version:

   ```sh
   gcloud secrets versions disable <n> --secret firebid-$ENV-<name>
   ```

For a planned rotation, the new credential works alongside the old one until step 4, so estimators notice nothing. Planned rotations respect the deployment guard (`firebid-ops deploy-guard`). An emergency rotation after a leak does not wait for the guard.

| Secret | Rotate | Read by | Retire the old one |
|---|---|---|---|
| `database-app-password` (`firebid_app`) | 90 days, or on leak | api, worker, sandbox, pgbouncer | the role has one password: see below |
| `database-service-password` (`firebid_service`) | 90 days, or on leak | api, worker | as above |
| `database-owner-password` (`postgres`) | 90 days, or on leak | the migration Job only | `gcloud sql users set-password postgres ...` |
| `storage-hmac-key` and `storage-hmac-secret` | 90 days, or on leak | api, worker, sandbox | `gcloud storage hmac update <old id> --deactivate`, then delete |
| `anthropic-api-key`, `openai-api-key`, `gemini-api-key` | 90 days, when someone who could see them leaves, or on leak | api, worker | revoke in the provider's console |
| `oidc-client` | per the IdP's expiry (Entra ID client secrets expire) | the IdP registration | delete the old secret in Entra ID |
| KMS key `firebid-<env>-data` (CMEK) | automatic, every 90 days (Terraform `rotation_period`) | Cloud SQL, buckets | old key versions stay enabled; never destroy them, or data encrypted with them is lost |

## Database role passwords

A PostgreSQL role has one password, so a rotation has a short window: pods started before the change fail to open new connections until they restart. Do it in a quiet period, in this order:

1. Generate the new password and add it to Secret Manager:

   ```sh
   openssl rand -base64 32 | tr -d '\n' | gcloud secrets versions add firebid-$ENV-database-app-password --data-file=-
   ```
2. Set it on the role, as the owner:

   ```sh
   ALTER ROLE firebid_app PASSWORD '...'
   ```
3. Immediately run `sync-secrets.sh`, then restart the pooler first and the rest after:

   ```sh
   kubectl -n firebid rollout restart deployment/pgbouncer
   kubectl -n firebid rollout restart deployment/firebid-api deployment/firebid-worker deployment/firebid-sandbox
   ```

   Connections through PgBouncer that are already open continue.
4. Check `/health`, then disable the old Secret Manager version.

## Storage HMAC keys

Create the new key for the storage service account:

```sh
gcloud storage hmac create <service account>
```

Add its access ID and secret as new versions, then sync and restart. Once the logs show no requests signed with the old key (about a day), deactivate the old key, then delete it.

## LLM provider keys

Create the new key in the provider console, scoped to the same project or workspace. Add it, sync, then restart the api and worker. The game day shows the gateway falls back if a key fails mid-rotation. Then revoke the old key in the console.

## After a leak

- Rotate first and investigate after. Treat the leak as S1 ([incident-response.md](incident-response.md)).
- Find where the leaked value was used: Cloud Audit Logs for Secret Manager access, and the provider's usage page. Record it in the incident log.
- If the leaked value was committed to git, rotating is the fix. Rewriting history does not un-leak it. gitleaks runs in CI to stop the next one.

## Record

Log each rotation (date, secret, who, and why) in the operations log. Evidence of the quarterly check goes in `docs/security/secrets-audit.md`.
