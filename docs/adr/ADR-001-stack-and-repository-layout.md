# ADR-001: Stack and repository layout

- **Status:** Proposed
- **Date:** 2026-09-22
- **Deciders:** Tech Lead; Product Owner consulted
- **Requirements:** §12.1; NFR-06, NFR-11, NFR-13, NFR-14; risks R10, R13

## Context

A team of 4–6 engineers, working with AI coding agents, builds a platform that mixes CRUD workflows, CPU-heavy drawing geometry, LLM calls and a data-dense web UI. The stack must be robust, maintainable by a small team, and fast on large drawing sets. The architecture review of 22 September 2026 reduced the number of moving parts.

## Options

1. **Python + TypeScript monorepo with one PostgreSQL** (below). The geometry, CAD, BIM and AI ecosystems are strongest in Python; one database covers relational data, the job queue and vector search.
2. **Polyglot services** (e.g. a Go API, Python workers, a separate vector database, a message broker). More moving parts to run, secure and back up; no benefit at this scale.
3. **Low-code or an off-the-shelf takeoff tool with extensions.** Fastest start, but it cannot meet the evidence, guardrail and multi-provider requirements (§6, §9, §12.2).

## Recommendation

Option 1:

| Concern | Choice |
|---|---|
| Backend | Python 3.14 (pinned; see the change below), FastAPI, Pydantic v2, `uv` |
| Database | PostgreSQL 17 + pgvector; SQLAlchemy 2.0 with **synchronous** sessions on psycopg 3; Alembic owns all schema |
| Jobs | PostgreSQL-backed queue (ADR-006); agents as plain functions (ADR-007) |
| Frontend | React 19 + TypeScript (strict) + Vite, TanStack Query/Table/Virtual, Tailwind + shadcn/ui (Radix); API client generated from OpenAPI |
| Identity | Microsoft Entra ID; Keycloak locally with Entra-shaped tokens |
| Local S3 | **SeaweedFS** (see below); production uses the cloud provider's object storage |
| Dev environment | VS Code Dev Container on the production base image (`python:3.14-slim-bookworm`) |
| Upkeep | `uv.lock`, `package-lock.json`, Renovate |

Layout: `backend/`, `frontend/`, `infra/`, `scripts/`, `docs/{requirements,plan,adr}/`, `.devcontainer/`, as in `docs/plan/project-context.md`.

**Change from the plan: SeaweedFS instead of MinIO.** The `minio/minio` image is no longer published on Docker Hub, and the last Quay build is from September 2025, so it receives no security fixes. SeaweedFS (Apache-2.0) is actively maintained (4.47, built September 2026) and speaks the same S3 API. Only local development uses it.

**Change of 4 October 2026: Python 3.14 instead of 3.12.** Python 3.12 was pinned because geometry and BIM libraries publish pre-built packages for it first. Every dependency the platform uses now publishes them for 3.14, so the pin moved, at the owner's request: `requires-python`, `.python-version`, the lint and type-check targets, the lock file and the backend, sandbox and Dev Container images. A BIM library added in Phase 3 must be checked for 3.14 packages before it is chosen.

**Synchronous database access.** Nearly all API work is short database transactions and all heavy work runs in job workers, so async would add complexity (lazy-loading pitfalls, two code styles) for no measurable gain. Streaming endpoints (progress via SSE) may use async.

## Consequences

- One language per tier and one database keep onboarding, operations and backups simple.
- CPU-bound work scales by adding worker processes, not threads.
- Every schema change, including the job queue's, goes through Alembic.
- Windows developers work inside the Dev Container; native tools behave as in production.
