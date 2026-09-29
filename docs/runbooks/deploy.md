# Runbook: deploy a release

**Who:** the platform engineer on duty, with a second person watching (production).
**When:** only in a window the deployment guard allows (NFR-03). Never within 48 hours of an active bid's submission deadline.
**Where:** Google Cloud, `asia-southeast1`. Staging first, then production (ADR-008).

## Before the window

1. **CI is green on the release commit.**
   - Every job has passed, including "Security scans" and the image Trivy scan.
   - The release is tagged `vYYYY.MM.DD-N`. That tag is `RELEASE` below.
2. **Check the window with the deployment guard.**
   Run it against the target environment's database, through Cloud SQL Auth Proxy, as the service role:

   ```sh
   FIREBID_DATABASE_URL=postgresql://firebid_service:...@127.0.0.1:5432/firebid \
     firebid-ops deploy-guard --start 2026-10-10T18:00Z --end 2026-10-10T22:00Z
   ```

   Exit 0 means the window is clear. Exit 1 means it is refused and the output lists the bids due. Pick another window; do not override the guard. Its evidence goes to `eval/results/ops/`.
3. **Tell the estimators.** Post the window in the team channel at least one working day ahead.
4. **Read the release notes for migrations.** An expand-only migration can run while the old release serves. A contract migration (dropping or renaming anything) must be in a later release than the code that stopped using it.

## Build and push the images

```sh
REGISTRY=asia-southeast1-docker.pkg.dev/$PROJECT/firebid-$ENV
for image in backend sandbox frontend; do
  docker build -t $REGISTRY/firebid-$image:$RELEASE -f <its Dockerfile> <its context>
  docker push $REGISTRY/firebid-$image:$RELEASE
done
```

The backend Dockerfile builds the backend image. `infra/sandbox/Dockerfile` builds the sandbox image, and `frontend/Dockerfile` the frontend. CI runs Trivy on the same Dockerfiles.

## Infrastructure changes (only when `infra/terraform` changed)

```sh
cd infra/terraform/envs/$ENV
terraform init
terraform plan -var project_id=$PROJECT -var hostname=$HOSTNAME -out release.plan
terraform apply release.plan
```

A second person reads the plan before `apply` in production. Stop if the plan destroys any of these, which should never happen in a routine release:

- the database;
- a bucket;
- a KMS key.

## Deploy

1. **Point kubectl at the cluster:**

   ```sh
   gcloud container clusters get-credentials firebid-$ENV --region asia-southeast1 --project $PROJECT
   ```
2. **Refresh the secrets from Secret Manager.** The command is idempotent; no value touches disk.

   ```sh
   infra/k8s/sync-secrets.sh $PROJECT $ENV $(terraform -chdir=infra/terraform/envs/$ENV output -raw database_private_ip)
   ```
3. **Migrate the schema.** Run the Job and wait for it to complete.

   ```sh
   sed -e "s|IMAGE|$REGISTRY/firebid-backend:$RELEASE|" -e "s|RELEASE|$RELEASE|" \
     infra/k8s/jobs/migrate.yaml | kubectl apply -f -
   kubectl -n firebid wait --for=condition=complete job/firebid-migrate-$RELEASE --timeout=15m
   ```

   If the Job fails, stop. Read its log (`kubectl -n firebid logs job/firebid-migrate-$RELEASE`). Each Alembic migration runs in one transaction, so a failed migration leaves the schema as it was, and the old release keeps serving.
4. **Roll out the workloads.** First set the overlay's `PROJECT` and `RELEASE` placeholders, then apply and watch:

   ```sh
   kubectl apply -k infra/k8s/overlays/$ENV
   for d in firebid-api firebid-worker firebid-sandbox firebid-frontend pgbouncer; do
     kubectl -n firebid rollout status deployment/$d --timeout=10m
   done
   ```

## Check

- `curl https://$HOSTNAME/health` returns `"status": "ok"`, with a fresh job queue heartbeat.
- Sign in as a test estimator in staging, or as yourself in production. Open a bid's workbench and its BOQ.
- **Staging only:** upload the synthetic test set (`firebid-eval` fixtures). Check that it is scanned, parsed and classified.
- In Cloud Monitoring, no alert policy has fired, and the parse queue depth returns to 0.

## Roll back

- **Images:** `kubectl -n firebid rollout undo deployment/<name>` for each deployment, or re-apply the overlay with the previous `RELEASE`.
- **Schema:** do not downgrade in production. The migrations are expand-first, so the previous release runs on the new schema. If a migration itself corrupted data, follow [restore.md](restore.md).
- Record the rollback and its cause in the incident log ([incident-response.md](incident-response.md)).

## Known gaps before the first apply

- The Terraform has been validated but never applied (ADR-008, "Consequences").
- The first staging deploy is also the acceptance run for two things: every call the object store makes through the Cloud Storage S3 API, and PgBouncer serving both database roles. `firebid-pgbouncer` logs in as `firebid_app`. Until pooling for the service role is confirmed, `sync-secrets.sh` points `FIREBID_DATABASE_SERVICE_URL` at Cloud SQL's private IP. The network policy lets the API and workers reach it on 5432.
