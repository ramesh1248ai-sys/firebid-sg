# Backend API and job worker image (same image, different commands).
# Base image matches the Dev Container, so native libraries behave identically.
FROM python:3.12-slim-bookworm

# numpy's BLAS sizes its thread pool from the host's CPUs, not the container's limit, and
# idle BLAS threads spin: with two jobs on 2 CPUs that made finding symbols 40 times
# slower (P1-11, ADR-010). One thread each; parallelism comes from processes.
ENV OPENBLAS_NUM_THREADS=1 \
    OMP_NUM_THREADS=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

# uv installs the dependencies at build time and is not shipped: it is mounted into
# the steps that need it, so the image carries no copy (NFR-06: nothing unused to patch).

WORKDIR /app

# Dependencies first, so code changes reuse the cached dependency layer.
COPY pyproject.toml uv.lock .python-version README.md ./
RUN --mount=from=ghcr.io/astral-sh/uv:0.11.25,source=/uv,target=/usr/local/bin/uv uv sync --frozen --no-dev --no-install-project

COPY alembic.ini ./
# The LLM routing table. It is read at runtime, and /health fails without it.
COPY config ./config
COPY src ./src
RUN --mount=from=ghcr.io/astral-sh/uv:0.11.25,source=/uv,target=/usr/local/bin/uv uv sync --frozen --no-dev

ARG GIT_SHA=unknown
ENV FIREBID_GIT_SHA=$GIT_SHA

RUN useradd --system --uid 10001 --no-create-home firebid
USER firebid

EXPOSE 8000
CMD ["uvicorn", "--factory", "firebid.api.app:app_factory", "--host", "0.0.0.0", "--port", "8000"]
