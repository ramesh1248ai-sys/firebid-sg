# FireBid SG: every developer and CI task. Run inside the Dev Container (see CLAUDE.md).
SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c

COMPOSE := docker compose -f infra/docker-compose.yml
# Inside the Dev Container the stack runs on the host; FIREBID_STACK_HOST=host.docker.internal.
STACK_HOST ?= $(or $(FIREBID_STACK_HOST),localhost)
PG_PORT ?= $(or $(FIREBID_PG_PORT),55432)
API_URL := http://$(STACK_HOST):$(or $(FIREBID_API_PORT),8000)
PHASE ?=
IDS ?=

.PHONY: help bootstrap up down logs ps lint typecheck test test-integration e2e api-client data-inventory golden-template eval eval-docs eval-gate exit-report security ingest-benchmark pipeline-benchmark req-coverage check

help: ## List targets
	@grep -E '^[a-z0-9-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-18s %s\n", $$1, $$2}'

bootstrap: ## Install backend, frontend and browser dependencies (Dev Container setup)
	# .git is shared with the host, so this one setting covers pushes from either side.
	git config core.hooksPath .githooks
	sudo chown -R "$$(id -u):$$(id -g)" frontend/node_modules 2>/dev/null || true
	cd backend && uv sync
	cd frontend && npm ci --no-audit --no-fund && npx playwright install --with-deps chromium

up: ## Build and start the local stack; wait until every service is healthy
	$(COMPOSE) up --build --detach --wait --wait-timeout 300
	@curl -fsS $(API_URL)/health && echo && echo "Stack healthy: app http://$(STACK_HOST):8080  API $(API_URL)"

down: ## Stop the local stack (data volumes are kept)
	$(COMPOSE) down

logs: ## Follow the stack's logs
	$(COMPOSE) logs --follow --tail=100

ps: ## Show stack services and their health
	$(COMPOSE) ps

lint: ## Lint backend and frontend
	cd backend && uv run ruff check . ../scripts && uv run ruff format --check . ../scripts
	cd frontend && npm run -s lint

typecheck: ## Type-check backend (mypy strict) and frontend (tsc)
	cd backend && uv run mypy
	cd frontend && npm run -s typecheck

test: ## Unit tests (no stack needed)
	# Database tests start their own PostgreSQL container. Inside the Dev Container that
	# container runs on the host, so testcontainers must be told where to reach it.
	cd backend && TESTCONTAINERS_HOST_OVERRIDE=$(STACK_HOST) uv run pytest
	cd frontend && npm run -s test

test-integration: ## Integration tests against the running stack (make up first)
	cd backend && FIREBID_DATABASE_URL=postgresql://firebid:firebid@$(STACK_HOST):$(PG_PORT)/firebid \
		FIREBID_API_URL=$(API_URL) FIREBID_STACK_HOST=$(STACK_HOST) uv run pytest -m integration

e2e: ## Playwright tests against the running stack (make up first)
	FIREBID_STACK_HOST=$(STACK_HOST) ./scripts/e2e.sh

data-inventory: ## Regenerate docs/data-inventory.md from the model metadata (NFR-07)
	uv run --project backend python scripts/data_inventory.py

api-client: ## Regenerate the frontend API client from the backend's OpenAPI schema
	cd backend && uv run python -m firebid.api.export_openapi ../frontend/src/api/openapi.json
	cd frontend && npm run -s api:generate

ingest-benchmark: ## Time a synthetic tender set through ingestion (NFR-01); SHEETS=300
	cd backend && uv run python -m firebid.evals.ingest_benchmark \
		--sheets $(or $(SHEETS),300) --report ../eval/results/ingest-throughput.md

pipeline-benchmark: ## Time a drawing set through the whole parse job on the running stack (NFR-01); PDF=<file> or SHEETS=<n>
	uv run --project backend python scripts/pipeline_benchmark.py \
		$(if $(PDF),--pdf "$(PDF)",--synthetic $(or $(SHEETS),20)) $(if $(OUT),--out "$(OUT)")

eval: ## Run the synthetic evaluation suite and write a report
	cd backend && uv run firebid-eval --root ../eval run --report ../eval/results/synthetic.md

eval-docs: ## Measure title block reading (FR-DOC-02); needs Tesseract for the OCR tenders
	cd backend && uv run firebid-eval --root ../eval run --suite doc_classification 		--report ../eval/results/doc_classification.md

security: ## Run the CI security scans locally (NFR-06): dependencies, code, secrets, IaC
	uv export --project backend --format requirements-txt --no-hashes --no-emit-project --frozen > .requirements.txt
	uvx pip-audit --strict --progress-spinner off -r .requirements.txt; rm -f .requirements.txt
	npm audit --prefix frontend --audit-level=high
	uvx bandit -r backend/src -q --severity-level high --confidence-level medium
	docker run --rm -v "$$PWD:/src" -w /src semgrep/semgrep semgrep scan --metrics off --config p/python --config p/typescript --config p/react --severity ERROR --error --exclude frontend/src/api/schema.d.ts backend/src frontend/src
	docker run --rm -v "$$PWD:/repo" zricethezav/gitleaks:latest detect --source /repo --redact --no-banner
	docker run --rm -v "$$PWD:/repo" aquasec/trivy:latest fs --quiet --exit-code 1 --severity HIGH,CRITICAL --scanners misconfig,secret --skip-dirs /repo/frontend/node_modules --skip-dirs /repo/backend/.venv --skip-files /repo/infra/keycloak/Dockerfile --skip-files /repo/infra/seaweedfs/Dockerfile /repo

exit-report: ## Write the Phase 1 exit report (P1-11); INDICATIVE=<dir> adds a real-drawing sample, LIVE=1 every bid's measures
	cd backend && uv run firebid-eval --root ../eval exit --out ../docs/reports/phase1-exit.md \
		$(if $(INDICATIVE),--indicative-root "$(INDICATIVE)") $(if $(LIVE),--live)

eval-gate: ## Fail if any metric has regressed against the accepted baseline (FR-LRN-01)
	cd backend && uv run firebid-eval --root ../eval compare

golden-template: ## Write the estimator workbook for golden-set collection (decision D3)
	cd backend && uv run firebid-eval template --out ../eval/templates/golden_takeoff.xlsx

req-coverage: ## Requirement coverage report; filter with PHASE=P1 or IDS=FR-QTO-08,NFR-06
	uv run --project backend python scripts/req_coverage.py $(if $(PHASE),--phase $(PHASE)) $(if $(IDS),--ids $(IDS))

check: lint typecheck test ## Everything that needs no running stack
