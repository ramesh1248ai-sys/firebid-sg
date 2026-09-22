# Backend API and job worker image (same image, different commands).
# Base image matches the Dev Container, so native libraries behave identically.
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

COPY --from=ghcr.io/astral-sh/uv:0.11.25 /uv /usr/local/bin/uv

WORKDIR /app

# Dependencies first, so code changes reuse the cached dependency layer.
COPY pyproject.toml uv.lock .python-version README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY alembic.ini ./
COPY src ./src
RUN uv sync --frozen --no-dev

ARG GIT_SHA=unknown
ENV FIREBID_GIT_SHA=$GIT_SHA

RUN useradd --system --uid 10001 --no-create-home firebid
USER firebid

EXPOSE 8000
CMD ["uvicorn", "--factory", "firebid.api.app:app_factory", "--host", "0.0.0.0", "--port", "8000"]
