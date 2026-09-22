# FireBid SG

Work inside the Dev Container (`.devcontainer/`); every command below runs there.

- Binding architecture, conventions and guardrails: `docs/plan/project-context.md` (read before any task)
- Requirements (IDs such as FR-QTO-08): `docs/requirements/FireBid_SG_Requirements_v2.md`
- What earlier steps built, and how to run them: `docs/plan/BUILD_LOG.md`
- Step order and build prompts: `docs/plan/IMPLEMENTATION_PLAN.md`, `docs/plan/prompts/`
- Decisions: `docs/adr/`

Commands: `make help` lists all. Common: `make check` (lint, types, unit tests), `make up` / `make down`
(local stack), `make test-integration`, `make e2e`, `make api-client` (after API changes),
`make req-coverage PHASE=P1`.
