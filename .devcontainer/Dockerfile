# Development image. Same base as the production backend and worker images (backend/Dockerfile),
# so native libraries behave identically in development, CI and production.
FROM python:3.14-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
 && apt-get install -y --no-install-recommends make git curl ca-certificates tesseract-ocr \
 && rm -rf /var/lib/apt/lists/*

# Node.js LTS, copied from the official image
COPY --from=node:24-bookworm-slim /usr/local/bin/node /usr/local/bin/node
COPY --from=node:24-bookworm-slim /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
 && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx

# uv, pinned (Renovate keeps it current)
COPY --from=ghcr.io/astral-sh/uv:0.12.23 /uv /uvx /usr/local/bin/

# The image runs as the person developing, not as root. The Dev Container feature
# `common-utils` is told the same name in devcontainer.json: it finds this user and gives it
# sudo, and `remoteUser` there is who every command runs as.
RUN groupadd --gid 1000 vscode \
 && useradd --uid 1000 --gid 1000 --create-home --shell /bin/bash vscode
USER vscode
