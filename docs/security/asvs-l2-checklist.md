# OWASP ASVS 5.0 Level 2: checklist

The Phase 1 review of FireBid SG against every chapter of the OWASP Application Security
Verification Standard 5.0 at Level 2, the target NFR-06 sets. The controls themselves, with
the test that keeps each one in force, are in [`docs/security-baseline.md`](../security-baseline.md).
This checklist says, chapter by chapter, whether Level 2 is met and on what evidence.

**Status key:**
- **Met:** built, tested, and in force on the local stack.
- **Met on deployment:** built, but in force only once the Google Cloud deployment exists (ADR-008, pending decision D2).
- **N/A:** the application has no such feature.
- **Open:** not met; an action is named.

Reviewed 29 Sep 2026 (P1-11), by the build agent, for the Phase 1 gate. An independent penetration test (scope: [`pen-test-scope.md`](pen-test-scope.md)) is due before production.

| Chapter | Status | Evidence, or what is missing |
|---|---|---|
| V1 Encoding and sanitization | Met | Pydantic validation on every request body; SQLAlchemy parameterised queries (ruff `S608`, Bandit `B608`); React's escaping, with no `dangerouslySetInnerHTML`; `defusedxml` for every XML parse; client workbooks patched at cell level, never with formulas built from input. |
| V2 Validation and business logic | Met | Domain state machines with guards (`domain/state_machines.py`); gates signed only by their accountable role; pricing only from a rate entry, enforced by a database constraint and trigger; import all-or-nothing with row-level error reports. |
| V3 Web frontend security | Met | CSP, `X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy`, COOP, CORP and `Permissions-Policy` on every response (`test_security_baseline.py::TestResponseHeaders`); one origin for the app and API; the token in session storage only. |
| V4 API and web service | Met | Typed OpenAPI schema; bodies capped (2 MB, 200 MB for uploads); a per-caller rate limit; errors never leak traces (`test_security_baseline.py`). |
| V5 File handling | Met | Malware scan before any parser; type decided by content signature; archive limits; pixel limits; every parser in the sandbox pool, with no network route and credentials stripped (`TestLimits`, `TestHostileContent`, `TestArchiveLimits`). |
| V6 Authentication | Met on deployment | Delegated to the identity provider (Keycloak locally, Microsoft Entra ID in production), where MFA is enforced (NFR-06). The app never holds a password. MFA enforcement is set in Entra ID, not verifiable locally. |
| V7 Session management | Met | Authorization code flow with PKCE; session storage (closing the tab ends the session); token expiry checked on every request. |
| V8 Authorization | Met | Bid membership checked in the application layer (404 to non-members) and by PostgreSQL row-level security; no table with a `bid_id` without a policy (tested); a per-action permission matrix (`auth/permissions.py`). |
| V9 Self-contained tokens | Met | JWTs verified against the provider's JWKS: signature, issuer, audience, expiry; `alg: none` and symmetric algorithms refused (`test_claims_and_tokens.py`). |
| V10 OAuth and OIDC | Met | Public client with PKCE; no client secret in the browser; claims mapped to the Entra token shape. |
| V11 Cryptography | Met on deployment | No home-made cryptography; SHA-256 for the audit chain and content hashes. Encryption at rest and managed keys are Cloud SQL and Cloud Storage defaults with CMEK in the Terraform (ADR-008). |
| V12 Secure communication | Met on deployment | TLS 1.2+ terminates at the Google Cloud load balancer with a managed certificate, and HSTS is already sent. The local stack is plain HTTP by design. |
| V13 Configuration | Met | Secrets by reference only (`llm.yaml`, Secret Manager in the Terraform); containers non-root with capabilities dropped; the dependency, container and IaC scans in CI (`Security scans` job); secrets audit clean ([`secrets-audit.md`](secrets-audit.md)). |
| V14 Data protection | Met | Personal-data columns declared and inventoried (`docs/data-inventory.md`); retention and archival on a schedule (`config/retention.yaml`, P1-11); LLM calls bounded by data class, with provider terms pending D2; logs carry IDs, not document text or prices. |
| V15 Secure coding and architecture | Met | mypy strict, ruff (Bandit rules), Bandit, Semgrep and oxlint in CI; dependencies pinned and updated by Renovate; `pip-audit` and `npm audit` fail the build on known vulnerabilities. |
| V16 Security logging and error handling | Met | Append-only audit log with a per-bid hash chain, now verified nightly (`system.verify_audit_chains`); a request ID on every log line; no secrets or bodies logged. |
| V17 WebRTC | N/A | No real-time media. |

## Open items at the Phase 1 gate

1. **An independent penetration test.** Scoped in [`pen-test-scope.md`](pen-test-scope.md); to run on staging before production (NFR-06: annual).
2. **MFA and TLS are verified only in the deployment.** Both are set outside the application code: MFA in Entra ID, TLS by the load balancer. The staging acceptance run checks them.
3. **`main` is not protected server-side.** Branch protection needs GitHub Pro on a private repository. The client-side `pre-push` hook is not a control.
4. **Shared rate limiting.** In-process per API instance until P2-08. Cloud Armor rate limits in front of the load balancer are part of the Terraform.
