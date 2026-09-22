# P0-03 · Identity, RBAC and Bid Workspace

**Builds on:** P0-02 (entities, state machines, audit log). **Needs:** decision D5 (gate approvers) for production role assignment. Seed roles from requirements §3 until then.

Begin by replying with a plan that maps each item under **Done when** to the work that satisfies it. Wait for approval before changing files.

## Read first

- `docs/plan/project-context.md`: guardrail 8 (per-bid scoping), auth choices.
- Requirements: §3 (personas, RACI), §5 (stages and gates), §6.1 (FR-BID), §6.16 (FR-ADM-01, 04), NFR-06, NFR-08.
- `docs/plan/BUILD_LOG.md`.

## Goal

Users sign in with OIDC and see only the bids they are members of. They can create a bid with its deadlines and track its stages and gates on a dashboard. Estimators and managers receive deadline alerts. Authorisation is enforced in one place, so every later endpoint inherits it.

## Scope

FR-BID-01, FR-BID-02, FR-BID-03, FR-ADM-01, FR-ADM-04 (viewer UI), NFR-06, NFR-08.

## Build

1. **OIDC:** validate JWTs from Keycloak (dev) or Entra ID (staging and production) with configuration only.
   - One claims mapper reads Entra's token shape (`oid`, `preferred_username`, `roles`). The dev Keycloak realm emits the same shape, so the code has no dev-only path.
   - Provision users on first sign-in. The IdP supplies identity only; bid membership and roles live in the app database.
   - Frontend sign-in and sign-out use the authorisation-code flow with PKCE.
2. **Permissions:** keep one permission matrix in code (action → roles) derived from the RACI in requirements §3. Gate approvals G0–G4 map to their Accountable roles. Organisation roles plus per-bid membership decide access.
3. **Scoping, enforced twice:**
   - **Application layer:** a FastAPI dependency and a repository-level query filter make every bid-owned query require membership. A non-member gets 404 for bid-owned resources.
   - **Database layer:** enable PostgreSQL row-level security on every bid-scoped table.
     - The policy admits rows whose `bid_id` belongs to the user set in a transaction-local setting (`SET LOCAL app.user_id`), checked against `bid_member`, which is indexed on (`user_id`, `bid_id`).
     - The app's database role has no `BYPASSRLS`.
     - Jobs run with the user or service identity that queued them.
     - Migrations and administrative tasks use a separate owner role.
4. **Bid workspace** (FR-BID-01, 02):
   - Create and edit a bid: client, project, tender reference, submission deadline, clarification cut-off, tender validity period, bid team.
   - The bid cannot leave Registered until these fields are complete.
   - Dashboard of active bids showing stage (S0–S9), gate status, open and overdue tasks, and days to each deadline.
   - Task model (assignee, due date, stage) linked to the bid.
5. **Deadline alerts** (FR-BID-03): a periodic job on the PostgreSQL job queue checks deadlines against intervals configured per organisation (e.g. 7, 3 and 1 days before). It notifies task owners through a `Notifier` interface with console and email adapters. The adapters are for internal staff only.
6. **Audit viewer** (FR-ADM-04 UI): filter by bid, actor, entity, action and date, with CSV export via the P0-02 API.
7. **Security baseline** (NFR-06): security headers, strict CORS, request size limits, rate limiting on auth-sensitive routes, secrets from the environment or a vault. Add a `docs/security-baseline.md` checklist mapping each item to OWASP ASVS Level 2 sections.

## Done when

- The FR-BID-01 acceptance criterion is automated: moving a bid out of Registered with a missing mandatory field fails; with all fields it succeeds.
- An automated test enumerates every registered API route. For each bid-owned route it asserts that a signed-in non-member gets 404. Tagged NFR-08. New routes added later fail this test until they are scoped.
- A row-level security test runs raw SQL as the app role, bypassing the application filter. It returns no rows from another bid, and no rows at all when `app.user_id` is unset. A second test enumerates every table with a `bid_id` column and fails if any lacks an RLS policy. Tagged NFR-08.
- The claims mapper passes the same tests with a Keycloak token and with a recorded Entra ID token.
- A time-frozen test shows alerts firing at each configured interval, and only to the relevant task owners. Tagged FR-BID-03.
- The Playwright E2E passes: sign in as Bid Manager → create a bid → see it on the dashboard with deadlines → sign in as an unrelated estimator → the bid is not visible.
- The permission matrix has a test asserting each gate (G0–G4) is approvable only by its Accountable role in §3. Tagged FR-ADM-01.
- A build log entry is appended.
