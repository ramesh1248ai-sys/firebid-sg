# Security baseline

What FireBid SG enforces, where it is enforced, and the test that keeps it enforced. Each row
names an OWASP ASVS 5.0 requirement at Level 2, the target for a system holding commercial
tender data and personal data under the PDPA (NFR-06, NFR-07, NFR-08).

A control with no test is a control that will quietly lapse, so every row here points at one.
Rows marked **Phase 1+** are not built yet; they name the step that will build them. The
chapter-by-chapter ASVS Level 2 review is [`docs/security/asvs-l2-checklist.md`](security/asvs-l2-checklist.md).

## Identity and session

| Control | Where | ASVS | Evidence |
|---|---|---|---|
| Sign-in is delegated to the organisation's identity provider; the app never holds a password | `frontend/src/auth/oidc.ts` | 2.1.1, 3.7.1 | `frontend/src/app/App.test.tsx` — signed-out redirect, sign-in hand-off |
| Authorization code flow with PKCE; no client secret reaches the browser | `frontend/src/auth/oidc.ts` | 51.2.1 | `oidcSettings` (public client, `response_type: code`) |
| The token lives in session storage only, so closing the tab ends the session | `frontend/src/auth/oidc.ts` | 3.2.1 | `WebStorageStateStore({ store: sessionStorage })` |
| Every token is verified against the provider's JWKS: signature, issuer, audience, expiry | `backend/src/firebid/auth/tokens.py` | 51.4.1, 51.4.3 | `backend/tests/auth/test_claims_and_tokens.py` — expiry, audience, issuer, wrong key, unsigned |
| Unsigned (`alg: none`) and symmetric tokens are refused | `backend/src/firebid/auth/tokens.py` | 51.4.2 | same file — `test_refuses_an_unsigned_token` |
| Roles come from the token but authority comes from the database | `backend/src/firebid/auth/provisioning.py` | 4.1.3 | `backend/tests/db/test_bid_workspace.py` |

## Access control

| Control | Where | ASVS | Evidence |
|---|---|---|---|
| Every bid route refuses a non-member with 404, not 403, so existence does not leak | `backend/src/firebid/api/deps.py` | 4.1.1, 4.1.5 | `test_bid_workspace.py` — the route walk over every bid-owned path |
| Row-level security enforces the same rule inside the database | `migrations/0003_row_level_security.py` | 4.1.1 | `test_row_level_security.py` — cross-bid read and write, and no acting user |
| Neither the application nor the service role can bypass row-level security | `migrations/0002`, `0003` | 4.1.1 | `test_row_level_security.py::test_neither_role_can_bypass` |
| A table holding `bid_id` cannot be added without a policy | migrations | 4.1.1 | `test_no_table_with_a_bid_id_is_left_without_a_policy` |
| Partitions created after the fact are protected too | `migrations/0006` | 4.1.1 | `test_a_partition_created_later_is_protected_too` |
| Each approval gate is signed only by its accountable role | `backend/src/firebid/auth/permissions.py` | 4.1.3 | `test_bid_workspace.py::TestPermissions` |

## Input and output

| Control | Where | ASVS | Evidence |
|---|---|---|---|
| Every request body is validated against a typed schema before a route runs | Pydantic models per router | 1.5.1 | route tests per endpoint |
| Request bodies are capped (2 MB; 200 MB for document uploads), by declared length and as they stream | `backend/src/firebid/api/security.py` | 13.4.1 | `test_security_baseline.py::TestBodySize` |
| Queries are parameterised; no SQL is built from caller input | SQLAlchemy throughout | 5.3.4 | `ruff` rule `S608` across the codebase |
| Errors answer with a message, never a stack trace or SQL | FastAPI handlers | 7.4.1 | `test_security_baseline.py` |

## Transport and browser

| Control | Where | ASVS | Evidence |
|---|---|---|---|
| `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, COOP, CORP, CSP and `Permissions-Policy` on every response, including refusals | `backend/src/firebid/api/security.py`, `frontend/nginx.conf` | 14.4.1–14.4.7 | `test_security_baseline.py::TestResponseHeaders` |
| HSTS is sent, for the moment a TLS terminator is in front | `security.py` | 14.4.5 | same |
| Cross-origin requests are refused unless an origin is configured; the app and API share an origin | `backend/src/firebid/api/app.py` | 14.5.3 | `test_security_baseline.py::TestCrossOrigin` |
| TLS 1.2+ terminates at the Google Cloud load balancer with a managed certificate; the local stack is plain HTTP | `infra/terraform` (ADR-008) | 9.1.1 | Terraform `google_compute_ssl_policy` (TLS 1.2 minimum); verified in staging, **pending D2** |

## Untrusted files

A tender set comes from outside the company and is read by parsers (`pypdfium2`, `ezdxf`,
Pillow, LibreOffice) that were written to be useful rather than to be attacked. Every one of
them runs in the sandbox pool, after the malware scan, and never in the API or the ordinary
worker (guardrail 9).

| Control | Where | ASVS | Evidence |
|---|---|---|---|
| Every uploaded file is scanned before any parser opens it; an outage holds files rather than passing them | `backend/src/firebid/ingest/scanning.py` | 12.4.2 | `test_document_ingestion.py` (EICAR quarantined, outage holds) |
| Type is decided by content signature, never by extension | `backend/src/firebid/ingest/detection.py` | 12.3.3 | `test_detection_and_archives.py::TestDetection` |
| Archive limits — total size, entry count, nesting depth, per-entry compression ratio — checked against declared sizes before anything is decompressed | `backend/src/firebid/ingest/archives.py` | 12.1.1 | `TestArchiveLimits` |
| Entry names are neutered, never used as paths | `ingest/archives.py` | 12.3.1 | `test_a_traversing_name_is_neutered_not_obeyed` |
| XML entity expansion and external entities refused (`defusedxml`) | `backend/src/firebid/sandbox/safety.py` | 5.5.2 | `TestHostileContent` |
| Image pixel limits (400 Mpx, warning raised as an error) | `sandbox/safety.py` | 12.1.1 | `test_an_oversized_image_is_refused` |
| Each parse runs in a fresh process with memory, CPU and wall-clock limits; a breach kills the job, never the pool | `backend/src/firebid/sandbox/runner.py` | 12.1.1 | `TestLimits` (each asserts the next job still runs) |
| The pool container is non-root, `cap_drop: ALL`, `no-new-privileges`, read-only except a `noexec,nosuid` tmpfs scratch | `infra/sandbox/Dockerfile`, `infra/docker-compose.yml` | 14.1.1 | `docker compose config` |
| **The pool has no route to the internet**: its network is `internal: true`, so it reaches postgres and seaweedfs and nothing else | `infra/docker-compose.yml` | 12.1.1 | verified with a connection attempt from the internal network |
| A parse child holds no credentials: the platform's environment is stripped before the file is opened | `sandbox/limits.py::drop_environment` | 2.10.4 | `test_a_job_does_not_inherit_the_platform_credentials` |
| Which walls actually stood is probed at startup and reported by `/health` | `sandbox/probe.py`, `api/checks.py` | 7.1.1 | `parser_sandbox` health check |

### What the per-job network wall is, and is not

Each parse child asks the kernel for its own empty network namespace
(`unshare(CLONE_NEWUSER | CLONE_NEWNET)`, with an identity uid map so the job keeps access to
its scratch). Where that is granted, the job genuinely has no network.

**Under Docker's default seccomp profile it is not granted** — `unshare` returns `EPERM` — so
in the local stack, and in any deployment using that profile, the per-job namespace does not
engage and the fallback is Python's socket layer being blocked. That fallback stops a parser
*using Python* to reach the network; it does not stop a C extension calling `connect(2)`
directly. This was measured, not assumed: a `ctypes` call to `connect(2)` from inside the
sandbox reached the network on a container with a default profile.

The wall that holds in every configuration is therefore the container's: the pool sits on an
`internal` Docker network with no gateway, so there is no route off it whatever a parser
does. The namespace is defence in depth on top of that, and `/health` reports whether it is
actually in force rather than assuming it.

Two ways to gain the stronger per-job wall, neither taken yet:

1. `security_opt: [seccomp=<profile>]` with Docker's default profile plus `unshare`. Targeted,
   at the cost of vendoring and maintaining a copy of that profile.
2. `seccomp=unconfined` with `cap_drop: ALL`, which permits `unshare` at the cost of the
   default syscall filter.

Open item 7 below tracks the decision.

## Availability

| Control | Where | ASVS | Evidence |
|---|---|---|---|
| A per-caller request budget, keyed on the bearer token rather than the IP address | `backend/src/firebid/api/security.py` | 11.2.1 | `test_security_baseline.py::TestRateLimit` |
| Health probes are never rate limited, so the limiter cannot take the service down | same | 11.2.1 | `test_health_checks_are_never_rate_limited` |
| A shared limiter (Redis or the ingress) when the API runs as more than one service | — | 11.2.1 | **Phase 2**, step P2-08; the in-process limiter is per-process until then |

## Record keeping

| Control | Where | ASVS | Evidence |
|---|---|---|---|
| Every state change is recorded with who, what, when and why | `backend/src/firebid/db/audit.py` | 16.2.1 | `test_transitions.py::TestAuditEventPerTransition` |
| History is append-only: the application role holds no UPDATE or DELETE, and a trigger refuses even the owner | `migrations/0002` | 16.3.3 | `test_audit_chain.py::TestAppendOnly` |
| A per-bid hash chain makes tampering detectable | `backend/src/firebid/db/audit.py` | 16.3.3 | `test_audit_chain.py` — an altered row breaks verification |
| One request ID threads every log line and is echoed to the caller | `backend/src/firebid/api/middleware.py` | 16.2.3 | `test_security_baseline.py::test_the_request_id_is_still_echoed` |
| Secrets, tokens and bodies are never logged | `logging.py`, `notifications.py`, `security.py` | 16.4.1 | `ConsoleNotifier` logs kind and subject only; the limiter keys on a hash |

## Data protection

| Control | Where | ASVS | Evidence |
|---|---|---|---|
| Personal-data columns are declared and inventoried for the PDPA | `backend/src/firebid/db/mixins.py`, `scripts/data_inventory.py` | 8.1.1 | `docs/data-inventory.md`, regenerated by `make data-inventory` |
| Documents are written once and never overwritten | `backend/src/firebid/storage/object_store.py` | 8.3.1 | `S3ObjectStore.put_once` |
| What each LLM provider may see is bounded by data class | `backend/config/llm.yaml` | 8.1.1 | **Phase 1**, step P1-01; decision D2 is still open |
| Retention and deletion on a schedule; audit kept at least 7 years and archived | `backend/config/retention.yaml`, `services/retention.py` | 8.3.2 | `test_retention.py` (P1-11); job `system.retention` |
| The audit hash chains are verified every night; a break raises an alert | `services/retention.py::verify_all_chains` | 16.3.3 | `test_retention.py::test_the_nightly_check_finds_a_tampered_chain`; job `system.verify_audit_chains` |

## Supply chain and build

| Control | Where | ASVS | Evidence |
|---|---|---|---|
| Dependencies are pinned by lock file and updated by Renovate | `uv.lock`, `package-lock.json`, `renovate.json` | 10.3.2 | CI installs from the lock files; the Renovate app is installed on this repository with Silent mode off, so updates arrive as pull requests |
| Static analysis runs on every push: `ruff` (including `bandit` rules), `mypy --strict`, `oxlint` | `.github/workflows/ci.yml` | 14.2.1 | CI |
| Work reaches `main` through a pull request with CI green | `.githooks/pre-push` | 1.1.2 | Local guard only; see the open item below |
| Dependency vulnerability scanning fails the build: `pip-audit` (Python), `npm audit` (high and above) | `.github/workflows/ci.yml` job `Security scans` | 10.3.3 | CI (P1-11); `make security` |
| Container images scanned; a high or critical vulnerability with a fix fails the build (Trivy) | CI job `Stack` | 10.3.3 | CI (P1-11). `uv` is mounted at build time and not shipped; the frontend's Alpine packages are upgraded |
| Static analysis for security: Bandit (high), Semgrep (Python, TypeScript, React) | CI job `Security scans` | 14.2.1 | CI (P1-11) |
| No secrets in the history (gitleaks) or the tree (Trivy); configuration and IaC misconfiguration (Trivy) | CI job `Security scans` | 13.3.1 | CI (P1-11); [`docs/security/secrets-audit.md`](security/secrets-audit.md) |

## Open items

These are known gaps, not oversights. Each names where it is closed.

1. **TLS in front of the stack** — local development is plain HTTP; the Terraform configures the load balancer and a managed certificate (P1-11, ADR-008). In force once staging exists (decision D2).
2. **Shared rate limiting** — the in-process limiter holds per process; it moves out when the API scales (P2-08).
3. ~~**Dependency scanning**~~ — closed in P1-11: dependency, container, code, secret and IaC scans fail the build.
4. ~~**Retention and deletion**~~ — closed in P1-11 for the platform's own data (`config/retention.yaml`); bid-level archival and deletion on request stays with P3-05.
5. **Provider data terms** — decision D2 must record what each LLM provider may retain before any confidential data class is routed to it.
6. **The per-job network namespace does not engage under Docker's default seccomp profile.** The pool's `internal` network is the wall that holds; the namespace is defence in depth and is currently inert locally. Closing it means choosing between a vendored seccomp profile and `seccomp=unconfined`, which is a security trade-off rather than a bug, and was decided in P1-11 (ADR-008): in Google Cloud the pool runs under GKE Sandbox (gVisor), whose own kernel isolates each pod, with a default-deny egress network policy. `/health` reports which walls are actually in force.
7. **`main` is not protected server-side.** GitHub refuses branch protection and rulesets on a private repository on the Free plan. `.githooks/pre-push` refuses a direct push to `main` so work goes through a pull request, but it is client-side and `--no-verify` bypasses it. Enforcement needs GitHub Pro; until then, treat a green PR as a convention rather than a gate.
