import createClient from "openapi-fetch";

import type { paths } from "./schema";

/**
 * Typed API client generated from the backend's OpenAPI schema (`make api-client`).
 * The API is served under /api on the app's own origin (Vite proxy in dev, nginx in the stack).
 */
export const api = createClient<paths>({
  baseUrl: new URL("/api", window.location.origin).toString(),
  // Resolve fetch at call time (not import time), so tests and instrumentation can wrap it.
  fetch: (request) => globalThis.fetch(request),
});
