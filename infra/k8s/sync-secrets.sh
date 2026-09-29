#!/usr/bin/env bash
# Build the cluster's Kubernetes secrets from Secret Manager (ADR-008; docs/runbooks/deploy.md).
#
#   infra/k8s/sync-secrets.sh <project> <staging|production> <cloud-sql-private-ip>
#
# Run by the operator deploying, with kubectl pointed at the environment's cluster. Values
# go straight from `gcloud` into `kubectl apply` through a pipe: nothing is written to disk
# or echoed. Rerun after rotating any secret (docs/runbooks/key-rotation.md), then restart
# the deployments that read it.
#
# Three secrets, each with only what its readers need:
#   firebid-secrets          api and worker: database, storage, LLM provider keys
#   firebid-sandbox-secrets  the parser pool: database and storage only, never a model key
#   firebid-pgbouncer        the connection pooler: where Cloud SQL is and how to log in
set -euo pipefail

project=${1:?project id}
environment=${2:?staging or production}
database_ip=${3:?Cloud SQL private IP (terraform output database_private_ip)}
prefix="firebid-${environment}"
pooler="pgbouncer.firebid.svc.cluster.local:6432"
# The pooler logs in as firebid_app. The service role (a few scheduled sweeps) goes to
# Cloud SQL directly until staging confirms PgBouncer serves both roles.

value() {
  gcloud secrets versions access latest --project "$project" --secret "${prefix}-$1"
}

app_password=$(value database-app-password)
service_password=$(value database-service-password)
hmac_key=$(value storage-hmac-key)
hmac_secret=$(value storage-hmac-secret)

apply() {
  kubectl apply -f - >/dev/null
  echo "applied $1"
}

kubectl create secret generic firebid-secrets --namespace firebid --dry-run=client -o yaml \
  --from-literal=FIREBID_DATABASE_URL="postgresql://firebid_app:${app_password}@${pooler}/firebid" \
  --from-literal=FIREBID_DATABASE_SERVICE_URL="postgresql://firebid_service:${service_password}@${database_ip}:5432/firebid" \
  --from-literal=FIREBID_S3_ACCESS_KEY_ID="$hmac_key" \
  --from-literal=FIREBID_S3_SECRET_ACCESS_KEY="$hmac_secret" \
  --from-literal=ANTHROPIC_API_KEY="$(value anthropic-api-key)" \
  --from-literal=OPENAI_API_KEY="$(value openai-api-key)" \
  --from-literal=GEMINI_API_KEY="$(value gemini-api-key)" \
  | apply firebid-secrets

kubectl create secret generic firebid-sandbox-secrets --namespace firebid --dry-run=client -o yaml \
  --from-literal=FIREBID_DATABASE_URL="postgresql://firebid_app:${app_password}@${pooler}/firebid" \
  --from-literal=FIREBID_S3_ACCESS_KEY_ID="$hmac_key" \
  --from-literal=FIREBID_S3_SECRET_ACCESS_KEY="$hmac_secret" \
  | apply firebid-sandbox-secrets

kubectl create secret generic firebid-pgbouncer --namespace firebid --dry-run=client -o yaml \
  --from-literal=POSTGRESQL_HOST="$database_ip" \
  --from-literal=POSTGRESQL_PORT=5432 \
  --from-literal=POSTGRESQL_USERNAME=firebid_app \
  --from-literal=POSTGRESQL_PASSWORD="$app_password" \
  | apply firebid-pgbouncer

# The migration job's own secret: the owner role, which only migrations use.
kubectl create secret generic firebid-migrate --namespace firebid --dry-run=client -o yaml \
  --from-literal=FIREBID_DATABASE_URL="postgresql://postgres:$(value database-owner-password)@${database_ip}:5432/firebid" \
  --from-literal=FIREBID_APP_DB_PASSWORD="$app_password" \
  --from-literal=FIREBID_SERVICE_DB_PASSWORD="$service_password" \
  | apply firebid-migrate
