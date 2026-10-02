# Secrets audit (P1-11, NFR-06)

Carried out 29 Sep 2026 on `main` plus the P1-11 branch.

## Method

1. **The whole git history,** 115 commits at the time, scanned with gitleaks (`make security`; in CI on every push).
2. **Code and configuration read by hand** for anything holding a credential: `backend/src/firebid/settings.py`, `backend/config/*.yaml`, `infra/docker-compose.yml`, `infra/.env.example`, the Keycloak realm export, and the frontend build configuration.
3. **Trivy's secret scanner** over the working tree (in CI).

## Findings

| Where | What | Verdict |
|---|---|---|
| Git history | gitleaks: **no leaks found** | Clean |
| `backend/config/llm.yaml` | Provider credentials by reference only (`env://ANTHROPIC_API_KEY`, `secret://...`) | Clean. In production the references resolve from Secret Manager (ADR-008). |
| `backend/src/firebid/settings.py` | Secret settings default to empty strings (`s3_secret_access_key = ""`) | Clean. Nothing usable is compiled in. |
| `infra/docker-compose.yml` | Development credentials in plain text: Postgres (`firebid`, `firebid-app`), SeaweedFS (`firebid-dev-secret`), Keycloak admin (`admin`), the dev users' password `firebid-dev` | **Accepted for development only.** The local stack binds to localhost, and none of these values exists in any deployment. The Terraform generates every production credential into Secret Manager and never uses these. |
| `infra/.env.example` | Placeholders only | Clean |
| Provider API keys | Passed into the containers from the host environment; never committed | Clean |
| Logs | Tokens, bodies and prices are never logged; the rate limiter keys on a hash of the token | Clean (see the security baseline, record keeping) |

## Rotation

Production secrets are held in Google Secret Manager. `infra/k8s/sync-secrets.sh` builds the cluster's secrets from it at each release, giving each workload only what it needs (the parser sandbox never holds a model key), and the pods read them at start-up. The procedure for rotating each kind (database roles, object storage keys, LLM provider keys, the OIDC client) is in [`docs/runbooks/key-rotation.md`](../runbooks/key-rotation.md).

## Result

No secret in the repository or its history can reach a deployment. The development credentials in the compose file are deliberate, local and documented. Nothing to rotate.
