# The parser pool: the only container that opens files from outside the company.
#
# It is deliberately the least privileged image in the stack. It holds LibreOffice (for legacy
# .doc and .xls) and the drawing parsers, and nothing that would be useful to someone who got
# code execution through a malformed tender drawing: no credentials, and no writable
# filesystem outside /scratch.
FROM python:3.12-slim-bookworm

# LibreOffice headless converts legacy Office formats (FR-DOC-01 item 5). The -nogui packages
# keep this to what is needed to convert, rather than a desktop suite.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libreoffice-writer-nogui \
        libreoffice-calc-nogui \
        fonts-dejavu-core \
        libgl1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

COPY --from=ghcr.io/astral-sh/uv:0.11.25 /uv /usr/local/bin/uv

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
    HOME=/scratch

WORKDIR /app

# Dependencies first, so code changes reuse the cached dependency layer.
COPY pyproject.toml uv.lock .python-version README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY config ./config
COPY src ./src
RUN uv sync --frozen --no-dev

ARG GIT_SHA=unknown
ENV FIREBID_GIT_SHA=$GIT_SHA

# Non-root, and the same uid as the worker so a shared scratch volume is usable by both.
RUN useradd --system --uid 10001 --no-create-home sandbox \
    && mkdir -p /scratch && chown 10001:10001 /scratch
USER 10001

# Everything except /scratch is read-only at runtime (see infra/docker-compose.yml).
VOLUME ["/scratch"]

CMD ["python", "-m", "firebid.jobs.worker", "--queues", "parse"]
