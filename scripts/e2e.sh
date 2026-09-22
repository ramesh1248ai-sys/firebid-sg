#!/usr/bin/env bash
# Playwright drives a real sign-in, and the identity provider issues tokens for the hostname
# the browser used. Inside the Dev Container the stack runs on the host, so forward the stack's
# ports onto localhost for the run: the browser then sees exactly what it sees in CI.
set -euo pipefail

host="${FIREBID_STACK_HOST:-localhost}"
forwarded=()

cleanup() {
  for pid in ${forwarded[@]+"${forwarded[@]}"}; do
    kill "$pid" 2>/dev/null || true
  done
}
trap cleanup EXIT

if [ "$host" != "localhost" ]; then
  for port in "${FIREBID_WEB_PORT:-8080}" "${FIREBID_KEYCLOAK_PORT:-8081}"; do
    socat "TCP-LISTEN:${port},fork,reuseaddr" "TCP:${host}:${port}" &
    forwarded+=("$!")
  done
  for port in "${FIREBID_WEB_PORT:-8080}" "${FIREBID_KEYCLOAK_PORT:-8081}"; do
    for _ in $(seq 1 50); do
      if (exec 3<>"/dev/tcp/localhost/${port}") 2>/dev/null; then break; fi
      sleep 0.1
    done
  done
fi

cd "$(dirname "$0")/../frontend" && npm run -s e2e
