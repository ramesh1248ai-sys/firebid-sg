# The parser pool: the only container that opens files from outside the company.
#
# It is deliberately the least privileged image in the stack. It holds LibreOffice (for legacy
# .doc and .xls) and the drawing parsers, and nothing that would be useful to someone who got
# code execution through a malformed tender drawing: no credentials, and no writable
# filesystem outside /scratch.
FROM python:3.12-slim-bookworm

# LibreOffice headless converts legacy Office formats (FR-DOC-01 item 5). The -nogui packages
# keep this to what is needed to convert, rather than a desktop suite. Tesseract reads title
# blocks drawn as outlines or scanned, and measures legibility (FR-DOC-02, FR-DOC-06).
RUN apt-get update && apt-get install -y --no-install-recommends \
        libreoffice-writer-nogui \
        libreoffice-calc-nogui \
        tesseract-ocr \
        tesseract-ocr-eng \
        fonts-dejavu-core \
        libgl1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# uv installs the dependencies at build time and is not shipped: it is mounted into
# the steps that need it, so the image carries no copy (NFR-06: nothing unused to patch).

# numpy's BLAS sizes its thread pool from the host's CPUs, not the container's limit, and
# idle BLAS threads spin: with two jobs on 2 CPUs that made finding symbols 40 times
# slower (P1-11, ADR-010). One thread each; parallelism comes from processes.
# MPLCONFIGDIR and HOME point at scratch because everything else is read-only at runtime, and
# both matplotlib and LibreOffice insist on writing somewhere.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    MPLCONFIGDIR=/scratch/matplotlib \
    HOME=/scratch \
    OPENBLAS_NUM_THREADS=1 \
    OMP_NUM_THREADS=1

WORKDIR /app

# Dependencies first, so code changes reuse the cached dependency layer.
COPY pyproject.toml uv.lock .python-version README.md ./
RUN --mount=from=ghcr.io/astral-sh/uv:0.11.25,source=/uv,target=/usr/local/bin/uv uv sync --frozen --no-dev --no-install-project

COPY config ./config
COPY src ./src
RUN --mount=from=ghcr.io/astral-sh/uv:0.11.25,source=/uv,target=/usr/local/bin/uv uv sync --frozen --no-dev

ARG GIT_SHA=unknown
ENV FIREBID_GIT_SHA=$GIT_SHA

# Non-root, and the same uid as the worker so a shared scratch volume is usable by both.
RUN useradd --system --uid 10001 --no-create-home sandbox \
    && mkdir -p /scratch && chown 10001:10001 /scratch
USER 10001

# Everything except /scratch is read-only at runtime (see infra/docker-compose.yml).
VOLUME ["/scratch"]

CMD ["python", "-m", "firebid.jobs.worker", "--queues", "parse"]
